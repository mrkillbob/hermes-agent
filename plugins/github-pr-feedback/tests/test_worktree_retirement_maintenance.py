"""Maintenance's public materializer shares the exact path owner fence."""
import pytest

from retirement_fixtures import git


def test_actual_maintenance_creation_and_reuse_refuse_retiring_workspace(retirement_linked, tmp_path):
    from github_pr_feedback.controller import LocalGitRepository
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, canonical_control_store, WorkspaceIdentity, WorktreeRetirementRefused

    source, _ = retirement_linked
    head = git(source, "rev-parse", "HEAD")
    materializer = LocalGitRepository(tmp_path / "maintenance")
    workspace = materializer.prepare_maintenance_worktree(source, "fixture/repo", head, "release")
    assert materializer.prepare_maintenance_worktree(source, "fixture/repo", head, "release") == workspace
    assert git(workspace, "rev-parse", "HEAD") == head
    before = git(source, "show-ref", "--heads")
    lifecycle = PathLifecycle(canonical_control_store())
    identity = WorkspaceIdentity("fixture/repo", str(source), str(workspace))
    assert lifecycle.snapshot(identity)["phase"] == "ready"
    generation = lifecycle.snapshot(identity)["generation"]
    with lifecycle.reserve_retirement(identity, generation):
        with pytest.raises(WorktreeRetirementRefused):
            materializer.prepare_maintenance_worktree(source, "fixture/repo", head, "release")
        assert git(source, "show-ref", "--heads") == before
        assert git(workspace, "rev-parse", "HEAD") == head
    assert lifecycle.snapshot(identity)["phase"] == "retiring"
