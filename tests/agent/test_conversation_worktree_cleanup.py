"""Fail-closed explicit cleanup for durable conversation worktrees."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from agent.conversation_worktree import (
    ConversationWorktreeError,
    ConversationWorktreeManager,
)
from agent.conversation_worktree_policy import ConversationWorktreePolicy
from hermes_state import SessionDB


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def prepared_binding(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    source = tmp_path / "stable"
    source.mkdir()
    git(source, "init")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "Hermes Test")
    git(source, "checkout", "-b", "stable")
    source.joinpath("tracked.txt").write_text("base\n", encoding="utf-8")
    git(source, "add", "tracked.txt")
    git(source, "commit", "-m", "base")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "-u", "origin", "stable")

    database = SessionDB(tmp_path / "state.db")
    policy = ConversationWorktreePolicy(
        enabled=True,
        source_worktree=source,
        worktree_root=tmp_path / "conversation-worktrees",
        branch_prefix="hermes/session",
        bootstrap=False,
        bootstrap_command=(),
        bootstrap_timeout=1.0,
        create_timeout=3.0,
        retain_until_explicit_cleanup=True,
    )
    manager = ConversationWorktreeManager(policy, database)
    binding = manager.bind_new_root_session("root", conversation_kind="interactive")
    assert binding is not None
    git(binding.path, "config", "user.email", "test@example.invalid")
    git(binding.path, "config", "user.name", "Hermes Test")

    sibling = tmp_path / "sibling-worktree"
    git(source, "worktree", "add", "-b", "sibling", str(sibling), binding.base_commit)

    yield manager, database, source, remote, binding, sibling

    database.close()


def commit_binding(binding, message: str = "conversation change") -> str:
    binding.path.joinpath("tracked.txt").write_text(f"{message}\n", encoding="utf-8")
    git(binding.path, "add", "tracked.txt")
    git(binding.path, "commit", "-m", message)
    return git(binding.path, "rev-parse", "HEAD")


def push_binding(binding) -> None:
    git(binding.path, "push", "origin", f"HEAD:refs/heads/{binding.branch}")


def integrate_binding(source: Path, binding) -> None:
    git(source, "merge", "--ff-only", binding.branch)


def certify_safe(source: Path, binding) -> None:
    commit_binding(binding)
    push_binding(binding)
    integrate_binding(source, binding)
    git(source, "push", "origin", "stable")


def test_cleanup_reports_dirty_unintegrated_and_unpushed_together(prepared_binding):
    manager, _db, _source, _remote, binding, _sibling = prepared_binding
    commit_binding(binding)
    binding.path.joinpath("untracked.txt").write_text("keep\n", encoding="utf-8")

    verdict = manager.inspect_cleanup(binding.root_session_id)

    assert verdict.allowed is False
    assert verdict.reasons == ("dirty", "unintegrated", "unpushed")
    assert binding.path.exists()


def test_cleanup_refuses_active_session_binding(prepared_binding):
    manager, _db, source, _remote, binding, _sibling = prepared_binding
    certify_safe(source, binding)

    verdict = manager.inspect_cleanup(
        binding.root_session_id, active_session_bound=True
    )

    assert verdict.allowed is False
    assert verdict.reasons == ("active",)


def test_cleanup_refuses_in_progress_git_state(prepared_binding):
    manager, _db, source, _remote, binding, _sibling = prepared_binding
    certify_safe(source, binding)
    merge_head = Path(git(binding.path, "rev-parse", "--git-path", "MERGE_HEAD"))
    merge_head.write_text(binding.base_commit + "\n", encoding="utf-8")

    verdict = manager.inspect_cleanup(binding.root_session_id)

    assert "in-progress" in verdict.reasons
    assert binding.path.exists()


def test_cleanup_distinguishes_unintegrated_from_unpushed(prepared_binding):
    manager, _db, source, _remote, binding, _sibling = prepared_binding
    commit_binding(binding)
    push_binding(binding)

    verdict = manager.inspect_cleanup(binding.root_session_id)

    assert "unintegrated" in verdict.reasons
    assert "unpushed" not in verdict.reasons
    assert source.exists()


def test_cleanup_refuses_integrated_but_unpushed_head(prepared_binding):
    manager, _db, source, _remote, binding, _sibling = prepared_binding
    commit_binding(binding)
    integrate_binding(source, binding)

    verdict = manager.inspect_cleanup(binding.root_session_id)

    assert "unintegrated" not in verdict.reasons
    assert "unpushed" in verdict.reasons


def test_cleanup_refuses_mismatched_worktree_identity(prepared_binding):
    manager, _db, _source, _remote, binding, _sibling = prepared_binding
    git(binding.path, "checkout", "--detach")

    verdict = manager.inspect_cleanup(binding.root_session_id)

    assert verdict.allowed is False
    assert verdict.reasons == ("mismatched identity",)
    assert binding.path.exists()


def test_cleanup_refuses_missing_remote_evidence(prepared_binding):
    manager, _db, _source, _remote, binding, _sibling = prepared_binding
    git(binding.path, "remote", "remove", "origin")

    verdict = manager.inspect_cleanup(binding.root_session_id)

    assert "missing remote evidence" in verdict.reasons
    assert binding.path.exists()


def test_cleanup_maps_git_inspection_failure_to_unknown(prepared_binding, monkeypatch):
    manager, _db, _source, _remote, binding, _sibling = prepared_binding
    original = manager._run_git

    def fail_status(cwd, args, timeout, phase):
        if args[:1] == ["status"]:
            raise ConversationWorktreeError("git inspection failed", phase=phase)
        return original(cwd, args, timeout, phase)

    monkeypatch.setattr(manager, "_run_git", fail_status)

    verdict = manager.inspect_cleanup(binding.root_session_id)

    assert verdict.allowed is False
    assert "unknown" in verdict.reasons
    assert binding.path.exists()


def test_explicit_cleanup_removes_only_exact_safe_worktree(prepared_binding):
    manager, db, source, _remote, binding, sibling = prepared_binding
    certify_safe(source, binding)
    claimed = db.get_conversation_worktree(binding.root_session_id)
    assert claimed is not None

    result = manager.remove_after_explicit_request(binding.root_session_id)

    assert result.removed is True
    assert result.verdict.allowed is True
    assert not binding.path.exists()
    assert sibling.exists()
    assert source.exists()
    assert git(source, "show-ref", "--verify", f"refs/heads/{binding.branch}")
    removed = db.get_conversation_worktree(binding.root_session_id)
    assert removed is not None
    assert removed.state == "removed"
    assert removed.worktree_path == claimed.worktree_path
    assert removed.branch == claimed.branch
    worktrees = git(source, "worktree", "list", "--porcelain")
    assert str(binding.path) not in worktrees
    assert str(sibling) in worktrees


def test_blocked_cleanup_never_removes_or_marks_binding(prepared_binding):
    manager, db, _source, _remote, binding, sibling = prepared_binding
    binding.path.joinpath("untracked.txt").write_text("keep\n", encoding="utf-8")

    result = manager.remove_after_explicit_request(binding.root_session_id)

    assert result.removed is False
    assert "dirty" in result.verdict.reasons
    assert binding.path.exists()
    assert sibling.exists()
    record = db.get_conversation_worktree(binding.root_session_id)
    assert record is not None
    assert record.state == "ready"
