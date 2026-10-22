"""Actual retained moves, durable uncertainty and immutable legacy history."""
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from retirement_fixtures import git, references


def make_plan(tmp_path, monkeypatch, source, workspace, *, ledger=None, slot_id=None, expected_lease=None):
    from github_pr_feedback import worktree_retirement as retirement
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity
    home = tmp_path / "control"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = home / "state" / "worktree-capacity.sqlite3"
    if not store.exists():
        with PathLifecycle(store).reserve_materialization(WorkspaceIdentity(
                "fixture/repo", str(source), str(tmp_path / "unrelated"))):
            pass
    identity = retirement.observe_identity(store, "fixture/repo", source, workspace, ledger=ledger, slot_id=slot_id)
    preservation, owner = references(tmp_path, identity)
    return retirement.prepare_retirement(identity, expected_lease, preservation, owner_reference=owner,
                                          receipt_path=tmp_path / "plan.json")


@pytest.mark.parametrize("stage", ["cross_store", "before_move", "after_move", "after_receipt"])
def test_retirement_failure_retains_intent_and_both_paths_are_protected(retirement_linked, tmp_path, monkeypatch, stage):
    from github_pr_feedback import worktree_retirement as retirement
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, PathOperation, WorktreeRetirementRefused

    source, workspace = retirement_linked
    plan = make_plan(tmp_path, monkeypatch, source, workspace)
    destination = tmp_path / "retained"
    (workspace / "late.ignored").write_bytes(b"preserve")
    original = retirement._git_run
    def failed(*args, **kwargs):
        raise RuntimeError("synthetic boundary failure")
    def move(*args):
        if args[1:3] == ("worktree", "move"):
            if stage == "before_move":
                failed()
            result = original(*args)
            if stage == "after_move":
                failed()
            return result
        return original(*args)
    if stage == "cross_store":
        monkeypatch.setattr(retirement, "_fence_pool", failed)
    if stage == "after_receipt":
        monkeypatch.setattr(PathOperation, "complete_move", failed)
    monkeypatch.setattr(retirement, "_git_run", move)
    with pytest.raises(RuntimeError, match="synthetic boundary failure"):
        retirement.retire_to_quarantine(plan, destination)
    lifecycle = PathLifecycle(Path(plan.identity.control_store))
    snapshot = lifecycle.snapshot(plan.identity.workspace_identity())
    assert snapshot["phase"] == "retiring"
    if stage == "cross_store":
        assert snapshot["destination"] is None
    else:
        assert snapshot["destination"] == str(destination)
    retained = destination if stage in {"after_move", "after_receipt"} else workspace
    assert (retained / "late.ignored").read_bytes() == b"preserve"
    with pytest.raises(WorktreeRetirementRefused):
        lifecycle.reserve_materialization(plan.identity.workspace_identity())
    if snapshot["destination"]:
        with pytest.raises(WorktreeRetirementRefused):
            lifecycle.reserve_materialization(replace(plan.identity.workspace_identity(), workspace=str(destination)))
    if stage == "after_receipt":
        result = Path(plan.receipt_path + ".result")
        before = result.read_bytes()
        assert json.loads(before)["disk_reclaimed"] == 0
        with pytest.raises(WorktreeRetirementRefused, match="retirement_result_occupied"):
            retirement.retire_to_quarantine(plan, destination)
        assert result.read_bytes() == before


def test_path_only_legacy_move_preserves_unknown_pool_history_verbatim(retirement_linked, tmp_path, monkeypatch):
    from github_pr_feedback import worktree_retirement as retirement
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity, canonical_control_store

    source, workspace = retirement_linked
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "control"))
    with PathLifecycle(canonical_control_store()).reserve_materialization(WorkspaceIdentity(
            "fixture/repo", str(source), str(workspace))):
        pass
    ledger = FeedbackLedger(tmp_path / "legacy.sqlite3")
    try:
        ledger._connection.execute("INSERT INTO worktree_pool_slots "
            "(slot_id,status,owner_pid,head_sha,claimed_at,updated_at,lease_version,task_id,board) "
            "VALUES (17,'free',22,?,'old-time','old-time',9,'historical-task','historical-board')", ("b" * 40,))
        before = ledger._connection.execute("SELECT * FROM worktree_pool_slots").fetchall()
        plan = make_plan(tmp_path, monkeypatch, source, workspace)
        assert plan.expected_lease is None and plan.identity.ledger is None
        assert plan.identity.physical_head != before[0][3]
        result = retirement.retire_to_quarantine(plan, tmp_path / "retained")
        assert result["disk_reclaimed"] == 0
        assert ledger._connection.execute("SELECT * FROM worktree_pool_slots").fetchall() == before
        assert not ledger._connection.execute("SELECT 1 FROM sqlite_master "
            "WHERE name='worktree_pool_identity'").fetchone()
    finally:
        ledger.close()


@pytest.mark.parametrize("change", ["head", "plan", "owner", "occupied_destination"])
def test_changed_identity_or_prepared_evidence_cannot_move_workspace(retirement_linked, tmp_path, monkeypatch, change):
    from github_pr_feedback import worktree_retirement as retirement
    from github_pr_feedback.worktree_lifecycle import WorktreeRetirementRefused

    source, workspace = retirement_linked
    plan = make_plan(tmp_path, monkeypatch, source, workspace)
    destination = tmp_path / "retained"
    if change == "head":
        git(workspace, "commit", "--quiet", "--allow-empty", "-m", "new occupant")
    elif change == "plan":
        plan = replace(plan, generation="0" * 48)
    elif change == "owner":
        Path(plan.owner_reference[0]).write_text("changed", encoding="utf-8")
    else:
        destination.mkdir()
        (destination / "owned").write_bytes(b"owned")
    with pytest.raises(WorktreeRetirementRefused):
        retirement.retire_to_quarantine(plan, destination)
    assert workspace.is_dir()
    if change == "occupied_destination":
        assert (destination / "owned").read_bytes() == b"owned"
    else:
        assert not destination.exists()


def test_associated_pool_move_preserves_historical_head_and_task_and_blocks_late_lease_writes(retirement_linked, tmp_path,
                                                                                           monkeypatch):
    from github_pr_feedback import controller, worktree_retirement as retirement
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.worktree_lifecycle import canonical_control_store, WorktreeRetirementRefused

    source, _ = retirement_linked
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "control"))
    monkeypatch.setattr(controller.LocalGitRepository, "_link_governed_venv", lambda *args: None)
    ledger = FeedbackLedger(tmp_path / "pool-ledger.sqlite3")
    pool = controller.PooledLocalGitRepository(ledger, tmp_path / "pool", slot_count=1)
    receipt = FeedbackReceipt("fixture/repo", 17, "review_comment", "120", git(source, "rev-parse", "HEAD"))
    try:
        prepared = pool.prepare_receipt_worktree(source, receipt)
        lease = pool._active_ledger_slots[receipt.key]
        pool.bind_task(receipt, "historical-task", "historical-board")
        pool.release(lease)
        ledger._connection.execute("UPDATE worktree_pool_slots SET head_sha=? WHERE slot_id=?", ("b" * 40, lease.slot_id))
        identity = retirement.observe_identity(canonical_control_store(), "fixture/repo", source,
                                               prepared.path, ledger=ledger.path, slot_id=lease.slot_id)
        expected = retirement._lease_snapshot(identity)
        assert expected["head_sha"] != identity.physical_head
        preservation, owner = references(tmp_path, identity)
        plan = retirement.prepare_retirement(identity, expected, preservation, owner_reference=owner,
                                              receipt_path=tmp_path / "plan.json")
        retirement.retire_to_quarantine(plan, tmp_path / "retained")
        assert retirement._lease_snapshot(identity) == expected
        with pytest.raises(WorktreeRetirementRefused):
            pool.release(lease)
        with pytest.raises(WorktreeRetirementRefused):
            pool.bind_task(receipt, "late-task", "other-board")
        assert retirement._lease_snapshot(identity) == expected
    finally:
        ledger.close()


def test_path_only_retirement_refuses_existing_pool_association(retirement_linked, tmp_path, monkeypatch):
    from github_pr_feedback import controller, worktree_retirement as retirement
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.worktree_lifecycle import canonical_control_store, WorktreeRetirementRefused

    source, _ = retirement_linked
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "control"))
    monkeypatch.setattr(controller.LocalGitRepository, "_link_governed_venv", lambda *args: None)
    ledger = FeedbackLedger(tmp_path / "pool-ledger.sqlite3")
    pool = controller.PooledLocalGitRepository(ledger, tmp_path / "pool", slot_count=1)
    receipt = FeedbackReceipt("fixture/repo", 17, "review_comment", "120", git(source, "rev-parse", "HEAD"))
    try:
        prepared = pool.prepare_receipt_worktree(source, receipt)
        assert receipt.key in pool._active_ledger_slots
        # A path-only operator plan must not erase the encoded ledger/slot
        # association and thereby skip the active-lease retirement fence.
        with pytest.raises(WorktreeRetirementRefused):
            retirement.observe_identity(canonical_control_store(), "fixture/repo", source, prepared.path)
    finally:
        ledger.close()


def test_path_only_retirement_reservation_rechecks_full_operation_identity(retirement_linked, tmp_path, monkeypatch):
    from github_pr_feedback import controller, worktree_retirement as retirement
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.worktree_lifecycle import (PathLifecycle, WorkspaceIdentity,
                                                       canonical_control_store, WorktreeRetirementRefused)

    source, _ = retirement_linked
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "control"))
    monkeypatch.setattr(controller.LocalGitRepository, "_link_governed_venv", lambda *args: None)
    ledger = FeedbackLedger(tmp_path / "pool-ledger.sqlite3")
    pool = controller.PooledLocalGitRepository(ledger, tmp_path / "pool", slot_count=1)
    receipt = FeedbackReceipt("fixture/repo", 17, "review_comment", "120", git(source, "rev-parse", "HEAD"))
    try:
        prepared = pool.prepare_receipt_worktree(source, receipt)
        lease = pool._active_ledger_slots[receipt.key]
        lifecycle = PathLifecycle(canonical_control_store())
        path_only = WorkspaceIdentity("fixture/repo", str(source), str(prepared.path))

        # Model a legacy path-only plan prepared before a pooled producer
        # republishes the row's full identity. Retirement must compare the
        # stored operation again inside its writer transaction.
        with lifecycle.reserve_materialization(path_only):
            pass
        identity = retirement.observe_identity(canonical_control_store(), "fixture/repo", source, prepared.path)
        preservation, owner = references(tmp_path, identity)
        plan = retirement.prepare_retirement(identity, None, preservation, owner_reference=owner,
                                              receipt_path=tmp_path / "plan.json")
        pooled_identity = WorkspaceIdentity("fixture/repo", str(source), str(prepared.path),
                                            str(ledger.path), lease.slot_id)
        with lifecycle.reserve_materialization(pooled_identity):
            pass

        with pytest.raises(WorktreeRetirementRefused):
            retirement.retire_to_quarantine(plan, tmp_path / "retained")
        assert prepared.path.is_dir()
        assert ledger._connection.execute(
            "SELECT status FROM worktree_pool_slots WHERE slot_id=?", (lease.slot_id,)
        ).fetchone()[0] == "leased"
        assert not (tmp_path / "retained").exists()
    finally:
        ledger.close()


@pytest.mark.parametrize("failure", ["capacity", "exact_head"])
def test_local_pre_mutation_failure_cancels_before_workspace_mutation(retirement_linked, tmp_path, monkeypatch, failure):
    from github_pr_feedback.controller import ExactHeadUnavailable, LocalGitRepository
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity, canonical_control_store
    from github_pr_feedback.worktree_capacity import WorktreeCapacityRejected

    source, _ = retirement_linked
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "control"))
    head = git(source, "rev-parse", "HEAD")
    receipt = FeedbackReceipt("fixture/repo", 17, "review_comment", "120", head)
    root = tmp_path / "local-worktrees"
    workspace = root / sha256("\x00".join(map(str, receipt.key)).encode("utf-8")).hexdigest()
    if failure == "capacity":
        class RefusingCapacity:
            def reserve(self, _workspace):
                raise WorktreeCapacityRejected("fixture capacity refusal")

        materializer = LocalGitRepository(root, capacity=RefusingCapacity())
        expected_error = WorktreeCapacityRejected
        expected_message = "fixture capacity refusal"
    else:
        materializer = LocalGitRepository(root)
        expected_error = ExactHeadUnavailable
        expected_message = "fixture exact head unavailable"

    def unavailable(*args):
        raise ExactHeadUnavailable("fixture exact head unavailable")

    monkeypatch.setattr(materializer, "_ensure_exact_head", unavailable)
    with pytest.raises(expected_error, match=expected_message):
        materializer.prepare_receipt_worktree(source, receipt)

    lifecycle = PathLifecycle(canonical_control_store())
    row = lifecycle.snapshot(WorkspaceIdentity("fixture/repo", str(source), str(workspace)))
    assert row["phase"] == "ready"
    assert not workspace.exists()


def test_maintenance_exact_head_failure_cancels_before_workspace_mutation(retirement_linked, tmp_path, monkeypatch):
    from github_pr_feedback.controller import LocalGitRepository
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity, canonical_control_store

    source, _ = retirement_linked
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "control"))
    head = git(source, "rev-parse", "HEAD")
    materializer = LocalGitRepository(tmp_path / "maintenance-worktrees")
    digest = sha256(f"fixture/repo\0{head}\0release".encode()).hexdigest()
    workspace = materializer._worktree_root / f"maintenance-{digest}"

    def missing_object(argv):
        assert argv[3] == "cat-file"
        raise RuntimeError("fixture exact maintenance head unavailable")

    monkeypatch.setattr(materializer, "_run", missing_object)
    with pytest.raises(RuntimeError, match="fixture exact maintenance head unavailable"):
        materializer.prepare_maintenance_worktree(source, "fixture/repo", head, "release")

    lifecycle = PathLifecycle(canonical_control_store())
    row = lifecycle.snapshot(WorkspaceIdentity("fixture/repo", str(source), str(workspace)))
    assert row["phase"] == "ready"
    assert not workspace.exists()
