"""Every supported materializer consults the same canonical exclusion authority."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


def protected(source, workspace, *, ledger=None, slot_id=None, state="retired"):
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity
    if state != "retired":
        from github_pr_feedback.worktree_lifecycle import canonical_control_store
        identity = WorkspaceIdentity("fixture/other" if state == "foreign" else "fixture/repo",
                                     str(source), str(workspace), str(ledger) if ledger else None, slot_id)
        lifecycle = PathLifecycle(canonical_control_store())
        if state == "uncertain":
            with pytest.raises(RuntimeError, match="unknown physical outcome"):
                with lifecycle.reserve_materialization(identity) as operation:
                    raise RuntimeError("unknown physical outcome")
        else:
            with lifecycle.reserve_materialization(identity) as operation:
                pass
        return operation
    from github_pr_feedback.worktree_lifecycle import materialization_for
    with materialization_for(source, workspace, "fixture/repo", ledger=ledger, slot_id=slot_id) as operation:
        pass
    with operation.lifecycle.reserve_retirement(operation.identity, operation.generation) as retirement:
        retirement.mark_destination(workspace.parent / (workspace.name + "-retained"))
        retirement.complete_move(workspace.parent / (workspace.name + "-retained"))
    return operation


@pytest.mark.parametrize("kind", ["local", "pool", "overflow", "branch", "maintenance"])
@pytest.mark.parametrize("state", ["retired", "uncertain", "foreign"])
@pytest.mark.parametrize("budget", [False, True])
def test_materializers_refuse_excluded_path_before_git_capacity_or_claim(tmp_path, monkeypatch, kind, budget, state):
    from github_pr_feedback import controller
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.worktree_lifecycle import WorktreeRetirementRefused

    source = tmp_path / "source"
    source.mkdir()
    root = tmp_path / "workspaces"
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    receipt = FeedbackReceipt("fixture/repo", 17, "review_comment", "120", "a" * 40)
    def forbidden(*args, **kwargs):
        pytest.fail("excluded materialization reached Git, budget admission or claim")
    # Even an injected alternate budget store cannot change path authority.
    capacity = SimpleNamespace(state_path=tmp_path / "alternate-budget.sqlite3", reserve=forbidden,
                               is_unregistered_existing=forbidden) if budget else None
    runner = SimpleNamespace(run=forbidden)
    try:
        if kind in {"local", "branch", "maintenance"}:
            target = root / ("maintenance-" + sha256(f"{receipt.repository}\0{receipt.head_sha}\0release".encode()).hexdigest()) if kind == "maintenance" else root / sha256("\0".join(map(str, receipt.key)).encode()).hexdigest()
            materializer = controller.LocalGitRepository(root, runner, capacity=capacity)
            protected(source, target, state=state)
        else:
            materializer = controller.PooledLocalGitRepository(ledger, root, runner, capacity=capacity)
            target = materializer._slot_path(receipt, 0, controller._pool_source_namespace(source))
            protected(source, target, ledger=ledger.path, slot_id=controller._pool_ledger_slot(
                controller._pool_source_namespace(source), 0), state=state)
        monkeypatch.setattr(ledger, "claim_worktree_slot", forbidden)
        with pytest.raises(WorktreeRetirementRefused):
            if kind == "maintenance":
                materializer.prepare_maintenance_worktree(source, receipt.repository, receipt.head_sha, "release")
            elif kind == "branch":
                materializer.prepare_receipt_branch(source, receipt)
            elif kind == "overflow":
                controller._prepare_receipt_worktree_with_overflow(materializer, source, receipt, tmp_path / "overflow")
            else:
                materializer.prepare_receipt_worktree(source, receipt)
    finally:
        ledger.close()


def test_unknown_legacy_pool_row_cannot_be_assigned_or_released_by_guess(tmp_path):
    from github_pr_feedback.ledger import FeedbackLedger, WorktreeSlotLease
    from github_pr_feedback.worktree_lifecycle import materialization_for, WorktreeRetirementRefused

    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    now = datetime.now(UTC)
    try:
        ledger._connection.execute("INSERT INTO worktree_pool_slots "
            "(slot_id,status,owner_pid,head_sha,claimed_at,updated_at,lease_version,task_id,board) "
            "VALUES (1,'free',22,?,?,?,7,'historical-task','historical-board')",
            ("b" * 40, now.isoformat(), now.isoformat()))
        before = ledger._connection.execute("SELECT * FROM worktree_pool_slots").fetchall()
        with pytest.raises(WorktreeRetirementRefused, match="legacy_pool_identity"):
            with materialization_for(tmp_path, tmp_path / "slot", "fixture/repo", ledger=ledger.path, slot_id=1):
                ledger.claim_worktree_slot(1, owner_pid=23, head_sha="a" * 40, claimed_at=now,
                                           stale_before=now - timedelta(hours=1))
        with pytest.raises(WorktreeRetirementRefused):
            ledger.finish_worktree_slot(WorktreeSlotLease(1, 7, 22))
        assert ledger._connection.execute("SELECT * FROM worktree_pool_slots").fetchall() == before
    finally:
        ledger.close()


@pytest.mark.parametrize("failure", ["fetch", "object_verification"])
def test_receipt_branch_verification_failure_leaves_reservation_retryable(
    tmp_path, retirement_linked, failure
):
    from github_pr_feedback.controller import LocalGitRepository
    from github_pr_feedback.git_commands import GitCommandResult, SubprocessGitRunner
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity, canonical_control_store
    from retirement_fixtures import git

    source, _ = retirement_linked
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    receipt = FeedbackReceipt("fixture/repo", 17, "review_comment", "pre-mutation-retry", head)
    root = tmp_path / "worktrees"
    repository = LocalGitRepository(root)
    branch = _receipt_branch_for_test(receipt)

    class FailingVerificationRunner:
        def __init__(self):
            self.cat_file_calls = 0

        def run(self, argv):
            if argv[3:5] == ["cat-file", "-e"]:
                self.cat_file_calls += 1
                if failure == "fetch" or self.cat_file_calls == 1:
                    return GitCommandResult(1, "", "missing object")
                return GitCommandResult(1, "", "object verification failed")
            if "fetch" in argv:
                if failure == "fetch":
                    return GitCommandResult(1, "", "offline fixture failure")
                return GitCommandResult(0, "")
            if argv[3:6] == ["rev-parse", "--verify", "--quiet"] and argv[-1] == "FETCH_HEAD^{commit}":
                return GitCommandResult(0, f"{head}\n")
            return SubprocessGitRunner().run(argv)

    repository._runner = FailingVerificationRunner()
    with pytest.raises(RuntimeError, match="exact head is unavailable"):
        repository.prepare_receipt_branch(source, receipt)

    identity = WorkspaceIdentity("fixture/repo", str(source), str(root / _local_key(receipt)))
    row = PathLifecycle(canonical_control_store()).snapshot(identity)
    assert row["phase"] == "ready"
    assert git(source, "show-ref", "--verify", f"refs/heads/{branch}", check=False) == ""

    repository._runner = SubprocessGitRunner()
    assert repository.prepare_receipt_branch(source, receipt) == branch
    assert git(source, "rev-parse", f"refs/heads/{branch}") == head


def test_receipt_branch_ref_created_before_runner_failure_remains_uncertain(
    tmp_path, retirement_linked
):
    from github_pr_feedback.controller import LocalGitRepository
    from github_pr_feedback.git_commands import SubprocessGitRunner
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity, canonical_control_store
    from retirement_fixtures import git

    source, _ = retirement_linked
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    receipt = FeedbackReceipt("fixture/repo", 17, "review_comment", "post-mutation-uncertain", head)
    root = tmp_path / "worktrees"
    repository = LocalGitRepository(root)
    branch = _receipt_branch_for_test(receipt)
    delegate = SubprocessGitRunner()

    class CreateThenFailRunner:
        def run(self, argv):
            result = delegate.run(argv)
            if "branch" in argv and result.returncode == 0:
                raise RuntimeError("branch command response was lost")
            return result

    repository._runner = CreateThenFailRunner()
    with pytest.raises(RuntimeError, match="branch command response was lost"):
        repository.prepare_receipt_branch(source, receipt)

    identity = WorkspaceIdentity("fixture/repo", str(source), str(root / _local_key(receipt)))
    row = PathLifecycle(canonical_control_store()).snapshot(identity)
    assert row["phase"] == "uncertain"
    assert git(source, "rev-parse", f"refs/heads/{branch}") == head


def _receipt_branch_for_test(receipt):
    from github_pr_feedback.controller import _receipt_branch
    return _receipt_branch(receipt)


def _local_key(receipt):
    return sha256("\0".join(map(str, receipt.key)).encode("utf-8")).hexdigest()
