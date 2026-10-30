"""Trusted operator preparation and retained Git move; no real pool is touched."""
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import pytest


from retirement_fixtures import git, references


@pytest.mark.platforms("posix")
def test_retained_git_move_preserves_late_bytes_index_and_open_descriptors(retirement_linked, tmp_path, monkeypatch):
    from github_pr_feedback import worktree_retirement as retirement
    from github_pr_feedback.worktree_lifecycle import PathLifecycle, WorktreeRetirementRefused

    source, workspace = retirement_linked
    # A real unmerged index, not a synthetic clean-state assertion.
    git(source, "checkout", "--quiet", "-b", "other")
    (source / "tracked").write_text("right\n", encoding="utf-8")
    git(source, "commit", "--quiet", "-am", "right")
    (workspace / "tracked").write_text("left\n", encoding="utf-8")
    git(workspace, "commit", "--quiet", "-am", "left")
    git(workspace, "merge", "other", check=False)
    conflicts = git(workspace, "ls-files", "--unmerged")
    assert conflicts
    store = tmp_path / "control" / "state" / "worktree-capacity.sqlite3"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "control"))
    from github_pr_feedback.worktree_lifecycle import WorkspaceIdentity
    with PathLifecycle(store).reserve_materialization(WorkspaceIdentity("fixture/repo", str(source), str(tmp_path / "unrelated"))):
        pass
    identity = retirement.observe_identity(store, "fixture/repo", source, workspace)
    preservation, owner = references(tmp_path, identity)
    plan = retirement.prepare_retirement(identity, None, preservation, owner_reference=owner,
                                          receipt_path=tmp_path / "plan.json")
    live = workspace / "open.ignored"
    with live.open("wb") as handle:
        handle.write(b"before\n")
        handle.flush()
        (workspace / "late.ignored").write_bytes(b"late ignored")
        (workspace / "untracked").write_bytes(b"late untracked")
        destination = tmp_path / "quarantine"
        result = retirement.retire_to_quarantine(plan, destination)
        handle.write(b"after move\n")
        handle.flush()
    assert not workspace.exists()
    assert (destination / "late.ignored").read_bytes() == b"late ignored"
    assert (destination / "untracked").read_bytes() == b"late untracked"
    assert (destination / "open.ignored").read_bytes() == b"before\nafter move\n"
    assert git(destination, "ls-files", "--unmerged") == conflicts
    assert git(destination, "rev-parse", "HEAD") == identity.physical_head
    assert result["disk_reclaimed"] == 0 and result["retained_mutable"] is True
    assert PathLifecycle(store).snapshot(identity.workspace_identity())["phase"] == "retired"
    with pytest.raises(WorktreeRetirementRefused):
        PathLifecycle(store).reserve_materialization(identity.workspace_identity())
    with pytest.raises(WorktreeRetirementRefused):
        retirement.retire_to_quarantine(plan, destination)
