"""Per-owner recovery must preserve unknown legacy rows and real mutation fences."""
from hashlib import sha256

import pytest

from retirement_fixtures import git


@pytest.mark.parametrize('refusal', ['legacy', 'retiring', 'unexpected_error'])
def test_reconciliation_preserves_refused_owner_and_reclaims_later_terminal_lease(
    retirement_linked, tmp_path, monkeypatch, refusal
):
    from github_pr_feedback.controller import LocalGitRepository, PooledLocalGitRepository
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, canonical_control_store

    source, _ = retirement_linked
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'control'))
    monkeypatch.setattr(LocalGitRepository, '_link_governed_venv', lambda *args: None)
    ledger = FeedbackLedger(tmp_path / 'ledger.sqlite3')
    pool = PooledLocalGitRepository(ledger, tmp_path / 'pool', slot_count=2)
    head = git(source, 'rev-parse', 'HEAD')
    try:
        if refusal == 'legacy':
            ledger._connection.execute('INSERT INTO worktree_pool_slots '
                '(slot_id,status,owner_pid,head_sha,claimed_at,updated_at,lease_version,task_id,board) '
                "VALUES (0,'leased',22,?,'legacy-time','legacy-time',7,'legacy-task','default')", (head,))
            blocked_slot = 0
        else:
            receipt = FeedbackReceipt('fixture/repo', 17, 'review_comment', 'first', head)
            pool.prepare_receipt_worktree(source, receipt)
            pool.bind_task(receipt, 'first-task', 'default')
            blocked_slot = pool._active_ledger_slots[receipt.key].slot_id
            if refusal == 'retiring':
                lifecycle = PathLifecycle(canonical_control_store())
                row = ledger._connection.execute('SELECT owner FROM worktree_pool_identity WHERE slot_id=?', (blocked_slot,)).fetchone()
                import json
                from github_pr_feedback.worktree_lifecycle import WorkspaceIdentity
                identity = WorkspaceIdentity(**json.loads(row[0]))
                snapshot = lifecycle.snapshot(identity)
            else:
                original_finish = ledger.finish_worktree_slot
                def unexpected(lease):
                    if lease.slot_id == blocked_slot:
                        raise OSError('fixture unexpected storage failure')
                    return original_finish(lease)
                monkeypatch.setattr(ledger, 'finish_worktree_slot', unexpected)
        receipt = FeedbackReceipt('fixture/repo', 18, 'review_comment', 'later', head)
        later = pool.prepare_receipt_worktree(source, receipt)
        pool.bind_task(receipt, 'later-task', 'default')
        later_slot = pool._active_ledger_slots[receipt.key].slot_id
        if refusal == 'retiring':
            lifecycle.reserve_retirement(identity, snapshot['generation'])
        before = ledger._connection.execute('SELECT * FROM worktree_pool_slots WHERE slot_id=?', (blocked_slot,)).fetchone()
        listed = ledger.leased_worktree_slots()
        assert listed[0]['slot_id'] == blocked_slot and listed[-1]['slot_id'] == later_slot

        class TerminalKanban:
            def task_status(self, board, task_id):
                return 'done'

        if refusal == 'unexpected_error':
            with pytest.raises(OSError, match='unexpected storage failure'):
                pool.reconcile_leases(TerminalKanban())
            expected_status = 'leased'
        else:
            assert pool.reconcile_leases(TerminalKanban()) == 1
            expected_status = 'free'
        assert ledger._connection.execute('SELECT * FROM worktree_pool_slots WHERE slot_id=?', (blocked_slot,)).fetchone() == before
        assert ledger._connection.execute('SELECT status FROM worktree_pool_slots WHERE slot_id=?', (later_slot,)).fetchone()[0] == expected_status
        assert later.path.is_dir()
        if refusal == 'legacy':
            assert not ledger._connection.execute('SELECT 1 FROM worktree_pool_identity WHERE slot_id=0').fetchone()
        elif refusal == 'retiring':
            assert lifecycle.snapshot(identity)['phase'] == 'retiring'
    finally:
        ledger.close()


@pytest.mark.parametrize(('caller', 'failure', 'phase'), [
    ('local', 'lookup', 'ready'),
    ('direct', 'lookup', 'ready'),
    ('nested', 'lookup', 'uncertain'),
    ('local', 'branch_response', 'uncertain'),
    ('direct', 'branch_response', 'uncertain'),
    ('nested', 'branch_response', 'uncertain'),
    ('local', 'workspace_response', 'uncertain'),
])
def test_branch_preflight_recovery_belongs_to_owner_and_mutation_stays_uncertain(
    retirement_linked, tmp_path, monkeypatch, caller, failure, phase
):
    from github_pr_feedback.controller import LocalGitRepository, _receipt_branch
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.worktree_lifecycle import (PathLifecycle, WorkspaceIdentity,
        WorktreeRetirementRefused, canonical_control_store, materialization_for)

    source, _ = retirement_linked
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'control'))
    monkeypatch.setattr(LocalGitRepository, '_link_governed_venv', lambda *args: None)
    receipt = FeedbackReceipt('fixture/repo', 17, 'review_comment', 'owner-recovery', git(source, 'rev-parse', 'HEAD'))
    root = tmp_path / 'worktrees'
    workspace = root / sha256('\0'.join(map(str, receipt.key)).encode()).hexdigest()
    repository = LocalGitRepository(root)
    branch = _receipt_branch(receipt)
    original = repository._run

    def fail(argv, **kwargs):
        if failure == 'lookup' and argv[3] == 'rev-parse':
            raise RuntimeError('fixture branch lookup failure')
        result = original(argv, **kwargs)
        if (failure == 'branch_response' and argv[3] == 'branch') or (failure == 'workspace_response' and argv[3:5] == ['worktree', 'add']):
            raise RuntimeError('fixture physical command response lost')
        return result

    monkeypatch.setattr(repository, '_run', fail)
    with pytest.raises(RuntimeError, match='fixture'):
        if caller == 'local':
            repository.prepare_receipt_worktree(source, receipt)
        elif caller == 'direct':
            repository.prepare_receipt_branch(source, receipt)
        else:
            with materialization_for(source, workspace, receipt.repository):
                repository.prepare_receipt_branch(source, receipt)
    identity = WorkspaceIdentity(receipt.repository, str(source), str(workspace))
    lifecycle = PathLifecycle(canonical_control_store())
    assert lifecycle.snapshot(identity)['phase'] == phase
    assert workspace.exists() == (failure == 'workspace_response')
    if failure == 'lookup':
        assert git(source, 'show-ref', '--verify', f'refs/heads/{branch}', check=False) == ''
    else:
        assert git(source, 'rev-parse', f'refs/heads/{branch}') == receipt.head_sha
    if phase == 'uncertain':
        with pytest.raises(WorktreeRetirementRefused):
            lifecycle.reserve_materialization(identity)
    else:
        monkeypatch.setattr(repository, '_run', original)
        if caller == 'local':
            assert repository.prepare_receipt_worktree(source, receipt).path == workspace
        else:
            assert repository.prepare_receipt_branch(source, receipt) == branch
        assert lifecycle.snapshot(identity)['phase'] == 'ready'
