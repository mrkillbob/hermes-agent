"""Evidence paths must survive a retained move without occupying its destination."""
from pathlib import Path

import pytest

from retirement_fixtures import references


@pytest.mark.parametrize("placement", ["workspace", "case_alias", "parent_alias", "occupied_result"])
def test_invalid_receipt_refuses_before_preparation_write(retirement_linked, tmp_path, monkeypatch, placement):
    from github_pr_feedback import worktree_retirement as retirement
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorkspaceIdentity, canonical_control_store, WorktreeRetirementRefused

    source, workspace = retirement_linked
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "control"))
    with PathLifecycle(canonical_control_store()).reserve_materialization(WorkspaceIdentity(
            "fixture/repo", str(source), str(workspace))):
        pass
    identity = retirement.observe_identity(canonical_control_store(), "fixture/repo", source, workspace)
    preservation, owner = references(tmp_path, identity)
    receipt = workspace / "plan.json"
    if placement == "case_alias":
        alias = workspace.with_name(workspace.name.upper())
        alias.mkdir(exist_ok=True)
        receipt = alias / "plan.json"
    elif placement == "parent_alias":
        receipt = source / ".." / "plan.json"
    elif placement == "occupied_result":
        receipt = tmp_path / "plan.json"
        Path(str(receipt) + ".result").write_bytes(b"preserved result")
    before = PathLifecycle(canonical_control_store()).snapshot(identity.workspace_identity())
    with pytest.raises(WorktreeRetirementRefused):
        retirement.prepare_retirement(identity, None, preservation, owner_reference=owner, receipt_path=receipt)
    assert not receipt.exists()
    assert workspace.is_dir()
    assert PathLifecycle(canonical_control_store()).snapshot(identity.workspace_identity()) == before


@pytest.mark.parametrize("collision", [True, False])
def test_result_destination_collision_refuses_before_intent_and_external_receipts_survive_move(
        retirement_linked, tmp_path, monkeypatch, collision):
    from github_pr_feedback import worktree_retirement as retirement
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorktreeRetirementRefused
    from test_worktree_retirement_failures import make_plan

    source, workspace = retirement_linked
    plan = make_plan(tmp_path, monkeypatch, source, workspace)
    lifecycle = PathLifecycle(plan.identity.control_store)
    before = lifecycle.snapshot(plan.identity.workspace_identity())
    destination = Path(plan.receipt_path + ".result") if collision else tmp_path / "retained"
    if collision:
        with pytest.raises(WorktreeRetirementRefused, match="receipt_overlaps_moving_workspace"):
            retirement.retire_to_quarantine(plan, destination)
        assert workspace.is_dir() and not destination.exists()
        assert lifecycle.snapshot(plan.identity.workspace_identity()) == before
    else:
        result = retirement.retire_to_quarantine(plan, destination)
        assert result["disk_reclaimed"] == 0
        assert Path(plan.receipt_path).is_file() and Path(plan.receipt_path + ".result").is_file()
        assert destination.is_dir() and not workspace.exists()
