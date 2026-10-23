"""Exact-path exclusion is separate from optional capacity charging."""
from dataclasses import replace
from pathlib import Path

import pytest


def test_materialization_and_retirement_share_durable_exact_path_fence(tmp_path):
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity, WorktreeRetirementRefused, WorktreeMaterializationBusy

    state = tmp_path / "state" / "worktree-capacity.sqlite3"
    identity = WorkspaceIdentity("fixture/repo", str(tmp_path / "source"), str(tmp_path / "slot"))
    lifecycle = PathLifecycle(state)
    with lifecycle.reserve_materialization(identity) as operation:
        assert operation.generation
        with pytest.raises(WorktreeMaterializationBusy):
            PathLifecycle(state).reserve_materialization(identity)
        with pytest.raises(WorktreeRetirementRefused):
            lifecycle.reserve_retirement(identity, operation.generation)
        other = replace(identity, workspace=str(tmp_path / "other"))
        with lifecycle.reserve_materialization(other):
            pass
    generation = lifecycle.snapshot(identity)["generation"]
    with lifecycle.reserve_retirement(identity, generation) as retirement:
        retirement.mark_destination(tmp_path / "retained")
        retirement.complete_move(tmp_path / "retained")
    for candidate in (identity, replace(identity, repository="fixture/other")):
        with pytest.raises(WorktreeRetirementRefused):
            PathLifecycle(state).reserve_materialization(candidate)
    assert lifecycle.snapshot(identity)["phase"] == "retired"


def test_failed_or_stale_operations_remain_protected_and_do_not_expire(tmp_path):
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity, WorktreeRetirementRefused

    lifecycle = PathLifecycle(tmp_path / "state" / "worktree-capacity.sqlite3")
    identity = WorkspaceIdentity("fixture/repo", str(tmp_path / "source"), str(tmp_path / "slot"))
    with pytest.raises(RuntimeError, match="synthetic"):
        with lifecycle.reserve_materialization(identity):
            raise RuntimeError("synthetic")
    assert lifecycle.snapshot(identity)["phase"] == "uncertain"
    with pytest.raises(WorktreeRetirementRefused):
        lifecycle.reserve_materialization(identity)
    alias = replace(identity, workspace=str(Path(identity.workspace).with_name("SLOT")))
    with pytest.raises(WorktreeRetirementRefused):
        lifecycle.reserve_materialization(alias)


def test_direct_pool_claim_requires_current_exact_materialization_operation(tmp_path):
    from datetime import UTC, datetime, timedelta
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.worktree_lifecycle import materialization_for, WorktreeRetirementRefused

    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    now = datetime.now(UTC)
    try:
        with pytest.raises(WorktreeRetirementRefused, match="exact_materialization_operation_required"):
            ledger.claim_worktree_slot(1, owner_pid=22, head_sha="a" * 40,
                                       claimed_at=now, stale_before=now - timedelta(hours=1))
        with materialization_for(tmp_path, tmp_path / "slot", "fixture/repo", ledger=ledger.path,
                                 slot_id=1) as operation:
            lease = ledger.claim_worktree_slot(1, owner_pid=22, head_sha="a" * 40,
                                               claimed_at=now, stale_before=now - timedelta(hours=1))
        before = ledger._connection.execute("SELECT * FROM worktree_pool_slots").fetchall()
        with operation.lifecycle.reserve_retirement(operation.identity, operation.generation):
            with pytest.raises(WorktreeRetirementRefused):
                ledger.finish_worktree_slot(lease)
            with pytest.raises(WorktreeRetirementRefused):
                ledger.bind_worktree_slot_task("a" * 40, "task", "board", slot_id=1, lease=lease)
        assert ledger._connection.execute("SELECT * FROM worktree_pool_slots").fetchall() == before
    finally:
        ledger.close()
