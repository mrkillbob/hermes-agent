"""Real-Git coverage for conversation-root worktree lifecycle ownership."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

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
def repo(tmp_path):
    path = tmp_path / "stable"
    path.mkdir()
    git(path, "init")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Hermes Test")
    (path / "tracked.txt").write_text("committed\n", encoding="utf-8")
    git(path, "add", "tracked.txt")
    git(path, "commit", "-m", "initial")
    return path


@pytest.fixture
def db(tmp_path):
    session_db = SessionDB(tmp_path / "state.db")
    try:
        yield session_db
    finally:
        session_db.close()


def policy(repo: Path, tmp_path: Path, **overrides) -> ConversationWorktreePolicy:
    values = {
        "enabled": True,
        "source_worktree": repo,
        "worktree_root": tmp_path / "conversation-worktrees",
        "branch_prefix": "hermes/session",
        "bootstrap": False,
        "bootstrap_command": (),
        "bootstrap_timeout": 1.0,
        "create_timeout": 2.0,
        "retain_until_explicit_cleanup": True,
    }
    values.update(overrides)
    return ConversationWorktreePolicy(**values)


def manager(repo: Path, db: SessionDB, tmp_path: Path, **overrides):
    return ConversationWorktreeManager(policy(repo, tmp_path, **overrides), db)


def test_binding_pins_committed_head_without_copying_dirty_stable_files(repo, db, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("do not copy\n", encoding="utf-8")

    binding = manager(repo, db, tmp_path).bind_new_root_session(
        "root-1", conversation_kind="interactive"
    )

    assert binding is not None
    assert binding.base_commit == base
    assert git(binding.path, "rev-parse", "HEAD") == base
    assert (binding.path / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert not (binding.path / "untracked.txt").exists()
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "dirty\n"
    assert (repo / "untracked.txt").read_text(encoding="utf-8") == "do not copy\n"


def test_task_kind_bypasses_conversation_isolation(repo, db, tmp_path):
    assert manager(repo, db, tmp_path).bind_new_root_session(
        "task", conversation_kind="task"
    ) is None
    assert db.get_conversation_worktree("task") is None


def test_bootstrap_failure_is_retained_and_never_ready(repo, db, tmp_path):
    worktree_manager = manager(
        repo,
        db,
        tmp_path,
        bootstrap=True,
        bootstrap_command=(sys.executable, "-c", "raise SystemExit(7)"),
    )

    with pytest.raises(ConversationWorktreeError, match="bootstrap"):
        worktree_manager.bind_new_root_session("root", conversation_kind="interactive")

    record = db.get_conversation_worktree("root")
    assert record is not None
    assert record.state == "creation_failed"
    assert record.failure_phase == "bootstrap"
    assert Path(record.worktree_path).exists()


def test_repeated_root_reuses_same_validated_ready_binding(repo, db, tmp_path):
    worktree_manager = manager(repo, db, tmp_path)
    first = worktree_manager.bind_new_root_session("root", conversation_kind="interactive")
    second = worktree_manager.bind_new_root_session("root", conversation_kind="interactive")

    assert first == second
    assert worktree_manager.resolve_existing_session("root") == first


def test_concurrent_same_root_claim_creates_one_binding(repo, tmp_path):
    path = tmp_path / "state.db"
    first_db = SessionDB(path)
    second_db = SessionDB(path)
    first_manager = manager(repo, first_db, tmp_path)
    second_manager = manager(repo, second_db, tmp_path)
    results = []
    errors = []

    def bind(worktree_manager):
        try:
            results.append(
                worktree_manager.bind_new_root_session(
                    "root", conversation_kind="interactive"
                )
            )
        except BaseException as exc:  # surfaced below with exact failure
            errors.append(exc)

    left = threading.Thread(target=bind, args=(first_manager,))
    right = threading.Thread(target=bind, args=(second_manager,))
    left.start()
    right.start()
    left.join(timeout=10)
    right.join(timeout=10)
    try:
        assert not left.is_alive()
        assert not right.is_alive()
        assert errors == []
        assert len(results) == 2
        assert results[0] == results[1]
        assert results[0] is not None
        assert results[0].path.exists()
    finally:
        first_db.close()
        second_db.close()


def test_common_dir_mismatch_is_rejected_before_reuse(repo, db, tmp_path):
    worktree_manager = manager(repo, db, tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    expected_path = tmp_path / "conversation-worktrees" / "conversation-root"
    db.claim_conversation_worktree(
        root_session_id="root",
        worktree_path=str(expected_path),
        branch="hermes/session/not-the-manager-branch",
        base_commit=base,
        repo_common_dir="/not/the/source/common-dir",
    )
    db.mark_conversation_worktree_ready("root")

    with pytest.raises(ConversationWorktreeError, match="identity"):
        worktree_manager.resolve_existing_session("root")


def test_existing_partial_path_is_retained_and_marked_failed(repo, db, tmp_path):
    worktree_manager = manager(repo, db, tmp_path)
    root_id = "root"
    expected_path, _ = worktree_manager._expected_identity(root_id)
    expected_path.mkdir(parents=True)
    expected_path.joinpath("partial.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ConversationWorktreeError, match="already exists"):
        worktree_manager.bind_new_root_session(root_id, conversation_kind="interactive")

    record = db.get_conversation_worktree(root_id)
    assert record is not None
    assert record.state == "creation_failed"
    assert expected_path.joinpath("partial.txt").exists()


def test_bootstrap_timeout_is_retained(repo, db, tmp_path):
    worktree_manager = manager(
        repo,
        db,
        tmp_path,
        bootstrap=True,
        bootstrap_command=(sys.executable, "-c", "import time; time.sleep(60)"),
        bootstrap_timeout=0.05,
    )

    with pytest.raises(ConversationWorktreeError, match="bootstrap"):
        worktree_manager.bind_new_root_session("root", conversation_kind="interactive")

    record = db.get_conversation_worktree("root")
    assert record is not None
    assert record.state == "creation_failed"
    assert record.failure_phase == "bootstrap"
    assert "timed out" in (record.failure_message or "")


def test_root_id_is_deterministically_sanitized_for_path_and_branch(repo, db, tmp_path):
    worktree_manager = manager(repo, db, tmp_path)
    binding = worktree_manager.bind_new_root_session(
        "root/../with spaces", conversation_kind="interactive"
    )

    assert binding is not None
    assert binding == worktree_manager.resolve_existing_session("root/../with spaces")
    assert binding.path.parent == tmp_path / "conversation-worktrees"
    assert git(repo, "check-ref-format", "--branch", binding.branch) == binding.branch


def test_bootstrap_runs_inside_worktree(repo, db, tmp_path):
    marker = tmp_path / "bootstrap-cwd.txt"
    script = (
        "from pathlib import Path; import os; "
        f"Path({str(marker)!r}).write_text(os.getcwd(), encoding='utf-8')"
    )
    binding = manager(
        repo,
        db,
        tmp_path,
        bootstrap=True,
        bootstrap_command=(sys.executable, "-c", script),
    ).bind_new_root_session("root", conversation_kind="interactive")

    assert binding is not None
    assert marker.read_text(encoding="utf-8") == str(binding.path)
