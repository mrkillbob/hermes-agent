"""Explicit retention preserves files; legacy remove never implies deletion."""

from dataclasses import asdict
from pathlib import Path
import subprocess
import os

import pytest

from agent.conversation_worktree import (
    ConversationWorktreeError, acquire_conversation_root_lease,
)
import hermes_state_runtime as rt
from tests.agent.test_conversation_worktree_cleanup import (
    git, git_worktree_is_locked, prepared_binding,
)
from tests.agent.test_conversation_worktree_environment_cleanup import usable_environment


@pytest.fixture
def retained_fixture(
    prepared_binding,  # health: allow F811 -- pytest injects the imported actual-Git fixture by parameter name
):
    manager, db, source, _remote, _old, _sibling = prepared_binding
    source.joinpath('.gitignore').write_text('/.venv/\n*.private\n')
    git(source, 'add', '.gitignore')
    git(source, 'commit', '-m', 'Retain directory-only environment ignore')
    git(source, 'push', 'origin', 'stable')
    environment = usable_environment(source.parent / 'venvs' / 'retention')
    source.joinpath('.venv').symlink_to(environment, target_is_directory=True)
    binding = manager.bind_new_root_session('retention', conversation_kind='interactive')
    db.create_session('retention', source='test')
    return manager, db, source, binding, environment


def lease(binding):
    return acquire_conversation_root_lease(
        root_session_id=binding.root_session_id, worktree_path=binding.path,
        repo_common_dir=binding.repo_common_dir, surface='retention-test',
    )


@pytest.mark.parametrize('rename,legacy', [(True, False), (False, False), (False, True)])
def test_retention_snapshots_verified_renamed_branch_without_rewriting_binding(retained_fixture, rename, legacy):
    import json
    from agent import conversation_worktree_retention as retention

    manager, db, source, binding, environment = retained_fixture
    root = binding.root_session_id
    original = asdict(db.get_conversation_worktree(root))
    renamed = 'review/retained-conversation' if rename else original['branch']
    if rename:
        git(binding.path, 'branch', '-m', renamed)
    refs = git(source, 'for-each-ref', '--format=%(refname) %(objectname)')
    tracked = binding.path.joinpath('tracked.txt').read_bytes()
    assert manager.inspect_cleanup(root).allowed

    result = manager.quarantine_after_explicit_request(root)
    assert result.outcome == 'retained' and not result.removed, result
    retained = Path(result.retained_path)
    record = db.get_conversation_worktree(root)
    data = json.loads(retention.receipt_path(record).read_text())
    assert data['branch_ref'] == 'refs/heads/' + renamed
    if legacy:
        del data['branch_ref']
        retention.receipt_path(record).write_text(json.dumps(data))
    assert data['binding']['branch'] == original['branch']
    assert all(getattr(record, key) == value for key, value in original.items()
               if key not in {'state', 'updated_at', 'failure_phase', 'failure_message'})
    assert retained.joinpath('tracked.txt').read_bytes() == tracked
    assert manager.reconcile_retention(root).outcome == 'retained'
    assert manager.quarantine_after_explicit_request(root).outcome == 'retained'
    restored = manager.restore_after_explicit_request(root)
    assert restored.outcome == 'restored' and not restored.removed, restored
    assert manager.restore_after_explicit_request(root).outcome == 'restored'
    assert git(binding.path, 'branch', '--show-current') == renamed
    assert git(source, 'for-each-ref', '--format=%(refname) %(objectname)') == refs
    assert binding.path.joinpath('tracked.txt').read_bytes() == tracked
    assert environment.joinpath('source-sentinel').read_text() == 'preserve source environment\n'
    assert db.get_conversation_worktree(root).branch == original['branch']


@pytest.mark.parametrize('change', ['during_move', 'after_retention', 'detached', 'malformed_receipt', 'missing_snapshot'])
def test_retention_rejects_branch_drift_after_snapshot_and_preserves_bytes(retained_fixture, monkeypatch, change):
    import json
    from agent import conversation_worktree_retention as retention

    manager, db, source, binding, environment = retained_fixture
    root = binding.root_session_id
    original_branch = db.get_conversation_worktree(root).branch
    renamed = 'review/accepted-before-retention'
    git(binding.path, 'branch', '-m', renamed)
    original_git = manager._run_git
    moves = []

    def actual_git(cwd, args, timeout, phase):
        result = original_git(cwd, args, timeout, phase)
        if args[:2] == ['worktree', 'move']:
            moves.append(args)
            if change == 'during_move':
                git(Path(args[-1]), 'branch', '-m', 'review/changed-after-snapshot')
        return result

    monkeypatch.setattr(manager, '_run_git', actual_git)
    result = manager.quarantine_after_explicit_request(root)
    retained = Path(result.retained_path)
    if change == 'during_move':
        assert result.outcome == 'unknown' and result.recovery_required
    else:
        assert result.outcome == 'retained', result
        if change == 'after_retention':
            git(retained, 'branch', '-m', 'review/changed-after-snapshot')
        elif change == 'detached':
            git(retained, 'checkout', '--detach', 'HEAD')
        else:
            path = retention.receipt_path(db.get_conversation_worktree(root))
            data = json.loads(path.read_text())
            if change == 'missing_snapshot':
                del data['branch_ref']
            else:
                data['branch_ref'] = 42
            path.write_text(json.dumps(data))
    before = retained.joinpath('tracked.txt').read_bytes()
    assert manager.reconcile_retention(root).outcome == 'unknown'
    assert manager.restore_after_explicit_request(root).outcome == 'unknown'
    assert len(moves) == 1 and not binding.path.exists()
    assert retained.joinpath('tracked.txt').read_bytes() == before
    assert environment.joinpath('source-sentinel').read_text() == 'preserve source environment\n'
    assert db.get_conversation_worktree(root).branch == original_branch
    assert git(source, 'show-ref', '--verify', 'refs/heads/' + (
        'review/changed-after-snapshot' if change in {'during_move', 'after_retention'} else renamed))


@pytest.mark.parametrize('late_write', [False, pytest.param(True, marks=pytest.mark.platforms('posix'))])
def test_explicit_retention_restore_preserves_bytes_identity_and_consumers(retained_fixture, monkeypatch, late_write):
    manager, db, source, binding, environment = retained_fixture
    original_record = asdict(db.get_conversation_worktree(binding.root_session_id))
    original_git = manager._run_git
    late = binding.path / 'late.private'
    handle = None

    def actual_git(cwd, args, timeout, phase):
        nonlocal handle
        if args[:2] == ['worktree', 'move'] and late_write and handle is None:
            handle = late.open('w+')
            handle.write('before native rename\n')
            handle.flush()
        return original_git(cwd, args, timeout, phase)

    monkeypatch.setattr(manager, '_run_git', actual_git)
    try:
        result = manager.quarantine_after_explicit_request(binding.root_session_id)
        assert not result.removed and result.outcome == 'retained', result
        retained = Path(result.retained_path)
        assert not binding.path.exists() and retained.is_dir()
        assert git_worktree_is_locked(source, retained)
        from agent.conversation_worktree_retention import receipt_path
        receipt = receipt_path(db.get_conversation_worktree(binding.root_session_id))
        if os.name != 'nt':
            assert receipt.stat().st_mode & 0o777 == 0o600
            assert receipt.parent.stat().st_mode & 0o777 == 0o700
        record = db.get_conversation_worktree(binding.root_session_id)
        assert record.state == 'retained' and record.worktree_path == str(binding.path)
        from tui_gateway.session_worktree_status import cleanup_status
        projection = cleanup_status(
            result.verdict, record, removed=result.removed, outcome=result.outcome,
            retained_path=result.retained_path, recovery_required=result.recovery_required,
        )
        assert projection['removed'] is False and projection['outcome'] == 'retained'
        assert projection['retained_path'] == str(retained)
        assert all(getattr(record, key) == value for key, value in original_record.items()
                   if key not in {'state', 'updated_at', 'failure_phase', 'failure_message'})
        if handle is not None:
            handle.write('after native rename\n')
            handle.flush()
            assert retained.joinpath('late.private').read_text() == 'before native rename\nafter native rename\n'
        assert retained.joinpath('.venv').resolve() == environment.resolve()
        assert manager.quarantine_after_explicit_request(binding.root_session_id).retained_path == str(retained)
        assert manager.reconcile_retention(binding.root_session_id).outcome == 'retained'
        with pytest.raises(ConversationWorktreeError):
            manager.resolve_existing_session(binding.root_session_id)
        with pytest.raises(ConversationWorktreeError):
            lease(binding)
        epoch = rt.begin_runtime_epoch(db, instance_id='retention-owner')
        with pytest.raises(rt.RuntimeStoreError, match='worktree_retained'):
            rt.admit_session_input(db, epoch=epoch, principal_id='owner', session_id='retention',
                                   request_id='retained', payload={'text': 'must not run'})
        restored = manager.restore_after_explicit_request(binding.root_session_id)
        assert restored.outcome == 'restored' and not restored.removed
        assert binding.path.is_dir() and not retained.exists()
        assert db.get_conversation_worktree(binding.root_session_id).state == 'ready'
        assert manager.restore_after_explicit_request(binding.root_session_id).outcome == 'restored'
        assert manager.resolve_existing_session(binding.root_session_id).path == binding.path
        root_lease = lease(binding)
        root_lease.release()
        assert environment.joinpath('source-sentinel').read_text() == 'preserve source environment\n'
    finally:
        if handle is not None:
            handle.close()


@pytest.mark.parametrize('fault', ['remove', 'automatic', 'busy', 'move_refused', 'after_move', 'db_commit', 'occupied_restore', 'corrupt_receipt', 'prior_intent', 'owner_changed'])
def test_retention_failures_preserve_evidence_and_do_not_report_removal(retained_fixture, monkeypatch, fault):
    manager, db, source, binding, environment = retained_fixture
    root = binding.root_session_id
    before = db.get_conversation_worktree(root)
    held = None
    if fault in {'remove', 'automatic'}:
        result = manager.remove_after_explicit_request(root, retain_for_retry=fault == 'automatic')
        assert result.outcome == 'refused' and not result.removed
        assert result.failure_message == 'Removal is disabled to preserve late files; request explicit quarantine.'
        assert db.get_conversation_worktree(root) == before and binding.path.is_dir()
        return
    if fault == 'busy':
        held = lease(binding)
    if fault == 'prior_intent':
        from hermes_state_worktree_cleanup import begin_cleanup
        epoch = rt.begin_runtime_epoch(db, instance_id='prior-owner')
        prior = begin_cleanup(db, epoch=epoch, root=root)
        result = manager.quarantine_after_explicit_request(root)
        assert result.outcome == 'unknown' and binding.path.is_dir()
        assert db._read_one('SELECT value FROM state_meta WHERE key=?', ('gateway.worktree_cleanup.v1.' + root,))[0] == prior
        return
    original_transition = db.transition_conversation_retention
    original_git = manager._run_git

    def actual_git(cwd, args, timeout, phase):
        if args[:2] == ['worktree', 'move']:
            if fault == 'move_refused':
                return subprocess.CompletedProcess(args, 1, '', 'synthetic native refusal')
            result = original_git(cwd, args, timeout, phase)
            if fault == 'owner_changed':
                from agent.conversation_worktree_retention import _marker
                _marker(manager, Path(args[-1])).write_text('{"foreign": true}')
            if fault == 'after_move':
                raise ConversationWorktreeError('synthetic uncertain native completion', phase='quarantine')
            return result
        return original_git(cwd, args, timeout, phase)

    monkeypatch.setattr(manager, '_run_git', actual_git)
    if fault == 'db_commit':
        monkeypatch.setattr(db, 'transition_conversation_retention', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('uncertain commit')))
    try:
        result = manager.quarantine_after_explicit_request(root)
        assert not result.removed
        if fault in {'occupied_restore', 'corrupt_receipt'}:
            assert result.outcome == 'retained'
            retained = Path(result.retained_path)
            if fault == 'occupied_restore':
                binding.path.mkdir()
                binding.path.joinpath('replacement').write_text('preserve replacement')
                assert manager.restore_after_explicit_request(root).outcome == 'refused'
                assert binding.path.joinpath('replacement').read_text() == 'preserve replacement'
            else:
                from agent.conversation_worktree_retention import receipt_path
                receipt_path(before).write_text('{malformed receipt')
                assert manager.reconcile_retention(root).outcome == 'unknown'
            assert retained.is_dir()
        elif fault in {'after_move', 'db_commit', 'owner_changed'}:
            assert result.outcome == 'unknown' and result.recovery_required
            assert Path(result.retained_path).is_dir() and not binding.path.exists()
            epoch = rt.begin_runtime_epoch(db, instance_id='uncertain-owner')
            with pytest.raises(rt.RuntimeStoreError, match='worktree_cleanup_pending'):
                rt.admit_session_input(db, epoch=epoch, principal_id='owner', session_id=root,
                                       request_id='unknown', payload={'text': 'must not run'})
            if fault == 'db_commit':
                monkeypatch.setattr(db, 'transition_conversation_retention', original_transition)
                assert manager.reconcile_retention(root).outcome == 'retained'
            else:
                assert manager.reconcile_retention(root).outcome == 'unknown'
        else:
            assert result.outcome == 'refused'
            assert binding.path.is_dir() and db.get_conversation_worktree(root).state == 'ready'
            assert git_worktree_is_locked(source, binding.path)
        assert environment.joinpath('source-sentinel').read_text() == 'preserve source environment\n'
    finally:
        if held is not None:
            held.release()


@pytest.mark.parametrize('stage', ['move_refused', 'restore', 'restore_refused', 'destination_occupied', 'changed_claim'])
def test_verified_cancellation_and_restore_retire_only_projected_claim(retained_fixture, monkeypatch, stage):
    from agent.conversation_worktree import _common_owner_claim_path
    from agent import conversation_worktree_retention as retention
    import json

    manager, db, _source, binding, _environment = retained_fixture
    root = binding.root_session_id
    record = db.get_conversation_worktree(root)
    original_claim = _common_owner_claim_path(Path(record.repo_common_dir), binding.path)
    original_bytes = original_claim.read_bytes()
    native = manager._run_git
    captured = {}
    project = retention._project_claim

    def project_claim(*args):
        projected = project(*args)
        captured['path'] = _common_owner_claim_path(Path(record.repo_common_dir), Path(projected.worktree_path))
        if stage == 'destination_occupied':
            Path(projected.worktree_path).mkdir()
            Path(projected.worktree_path, 'foreign').write_text('preserve')
        return projected

    def refused(cwd, args, timeout, phase):
        if args[:2] == ['worktree', 'move']:
            return subprocess.CompletedProcess(args, 1, '', 'native refusal')
        return native(cwd, args, timeout, phase)

    monkeypatch.setattr(retention, '_project_claim', project_claim)
    if stage == 'move_refused':
        monkeypatch.setattr(manager, '_run_git', refused)
    result = manager.quarantine_after_explicit_request(root)
    if stage in {'move_refused', 'destination_occupied'}:
        assert result.outcome == 'refused', result
        assert not captured['path'].exists()
        assert not retention.receipt_path(record).exists()
        assert db.get_conversation_worktree(root).state == 'ready'
        assert binding.path.is_dir()
        assert manager.resolve_existing_session(root).path == binding.path
        if stage == 'destination_occupied':
            assert Path(result.retained_path, 'foreign').read_text() == 'preserve'
    else:
        assert result.outcome == 'retained', result
        if stage == 'changed_claim':
            captured['path'].write_text(json.dumps({'foreign': True}))
            result = manager.restore_after_explicit_request(root)
            assert result.outcome == 'unknown'
            assert json.loads(captured['path'].read_text()) == {'foreign': True}
        else:
            if stage == 'restore_refused':
                monkeypatch.setattr(manager, '_run_git', refused)
                result = manager.restore_after_explicit_request(root)
                assert result.outcome == 'refused', result
                assert json.loads(retention.receipt_path(record).read_text())['phase'] == 'retained'
                assert captured['path'].exists()
                monkeypatch.setattr(manager, '_run_git', native)
            result = manager.restore_after_explicit_request(root)
            assert result.outcome == 'restored', result
            assert not captured['path'].exists()
            assert manager.restore_after_explicit_request(root).outcome == 'restored'
    assert original_claim.read_bytes() == original_bytes


@pytest.mark.parametrize('action,fault', [
    ('restore', 'receipt'), ('restore', 'unlink'), ('restore', 'sync'),
    ('restore', 'archive'), ('restore', 'archive_sync'), ('restore', 'release'),
    ('restore', 'new_intent'), ('restore', 'archived_new_intent'),
    ('cancel', 'unlink'), ('cancel', 'sync'), ('cancel', 'archive'),
    ('cancel', 'archive_sync'), ('cancel', 'release'),
])
def test_terminal_filesystem_faults_reconcile_exact_owned_bookkeeping(retained_fixture, monkeypatch, action, fault):
    from agent import conversation_worktree_retention as retention
    from hermes_state_worktree_cleanup import INTENT_PREFIX
    import json

    manager, db, _source, binding, _environment = retained_fixture
    root = binding.root_session_id
    native = manager._run_git
    if action == 'restore':
        assert manager.quarantine_after_explicit_request(root).outcome == 'retained'
    else:
        def refused(cwd, args, timeout, phase):
            if args[:2] == ['worktree', 'move']:
                return subprocess.CompletedProcess(args, 1, '', 'native refusal')
            return native(cwd, args, timeout, phase)
        monkeypatch.setattr(manager, '_run_git', refused)
    original = db.transition_conversation_retention
    committed = False

    def transition(*args, **kwargs):
        nonlocal committed
        result = original(*args, **kwargs)
        committed = True
        if fault == 'release' and not kwargs.get('retain_intent', False):
            raise OSError('uncertain final intent release')
        return result

    monkeypatch.setattr(db, 'transition_conversation_retention', transition)
    write, retire, sync, archive = retention._write, retention._retire_projected_claim, retention._sync_parent, retention._archive

    def write_receipt(path, data, **kwargs):
        if committed and fault == 'receipt':
            raise OSError('terminal receipt write failed')
        return write(path, data, **kwargs)

    def retire_claim(*args):
        if committed and fault == 'unlink':
            raise OSError('terminal claim unlink failed')
        return retire(*args)

    def sync_parent(path):
        if committed and fault == 'sync' and path.parent.name == 'hermes-conversation-owner-claims-v1':
            raise OSError('claim unlink fsync failed')
        if committed and fault in {'archive_sync', 'archived_new_intent'} and path.parent.name == 'hermes-conversation-retention-v1' and not path.exists():
            raise OSError('archive completed but fsync failed')
        return sync(path)

    def archive_receipt(*args):
        if committed and fault in {'archive', 'new_intent'}:
            raise OSError('terminal receipt archive failed')
        return archive(*args)

    monkeypatch.setattr(retention, '_write', write_receipt)
    monkeypatch.setattr(retention, '_retire_projected_claim', retire_claim)
    monkeypatch.setattr(retention, '_sync_parent', sync_parent)
    monkeypatch.setattr(retention, '_archive', archive_receipt)
    result = (manager.restore_after_explicit_request(root) if action == 'restore'
              else manager.quarantine_after_explicit_request(root))
    assert committed and result.outcome == 'unknown' and result.recovery_required, result
    assert binding.path.is_dir() and db.get_conversation_worktree(root).state == 'ready'
    if fault not in {'archive_sync', 'archived_new_intent'}:
        with pytest.raises(ConversationWorktreeError):
            lease(binding)
    monkeypatch.setattr(retention, '_write', write)
    monkeypatch.setattr(retention, '_retire_projected_claim', retire)
    monkeypatch.setattr(retention, '_sync_parent', sync)
    monkeypatch.setattr(retention, '_archive', archive)
    monkeypatch.setattr(db, 'transition_conversation_retention', original)
    if fault in {'new_intent', 'archived_new_intent'}:
        key = INTENT_PREFIX + root
        saved = db._read_one('SELECT value FROM state_meta WHERE key=?', (key,))
        replacement = json.dumps({'different': 'preserve this intent'})
        db._execute_write(lambda conn: conn.execute('INSERT OR REPLACE INTO state_meta(key,value) VALUES(?,?)', (key, replacement)))
        assert manager.reconcile_retention(root).outcome == 'unknown'
        assert db._read_one('SELECT value FROM state_meta WHERE key=?', (key,))[0] == replacement
        if saved is None:
            db._execute_write(lambda conn: conn.execute('DELETE FROM state_meta WHERE key=?', (key,)))
        else:
            db._execute_write(lambda conn: conn.execute('UPDATE state_meta SET value=? WHERE key=?', (saved[0], key)))
    recovered = manager.reconcile_retention(root)
    assert recovered.outcome == ('restored' if action == 'restore' else 'refused'), recovered
    assert not recovered.recovery_required
    assert not retention.receipt_path(db.get_conversation_worktree(root)).exists()
    assert db._read_one('SELECT value FROM state_meta WHERE key=?', (INTENT_PREFIX + root,)) is None
    assert manager.resolve_existing_session(root).path == binding.path
    held = lease(binding)
    held.release()
