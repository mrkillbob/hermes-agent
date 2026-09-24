"""CLI coverage for dispatcher-managed repository completion receipts."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban import _cmd_complete
from hermes_cli.kanban_completion_policy import CompletionPolicyError


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_cli_complete_rejects_false_no_change_receipt_for_ahead_worktree(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """`hermes kanban complete` cannot bypass the worktree receipt policy."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()

    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "cli-test@example.com")
    _git(repo, "config", "user.name", "CLI Test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")
    branch = "codex/cli-receipt"
    _git(repo, "checkout", "-b", branch)
    (repo / "tracked.txt").write_text("base\nchange\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "change")

    with kbc.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="CLI receipt",
            workspace_kind="worktree",
            workspace_path=str(repo),
            branch_name=branch,
        )
        conn.execute(
            "UPDATE tasks SET status = 'ready', workspace_base_ref = ?, "
            "workspace_base_sha = ? WHERE id = ?",
            ("main", base_sha, task_id),
        )
        conn.commit()

    args = argparse.Namespace(
        task_ids=[task_id],
        summary="incorrect no-change receipt",
        result=None,
        metadata=json.dumps({"repository_changes": False}),
        force=False,
    )
    assert _cmd_complete(args) == 1
    assert "assigned base" in capsys.readouterr().err
    with kbc.connect() as conn:
        assert kb.get_task(conn, task_id).status == "ready"


def test_never_dispatched_triage_worktree_can_complete_as_no_change(
    tmp_path: Path, monkeypatch,
) -> None:
    """A planned worktree is not a repository checkout until its task is dispatched."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    missing_workspace = tmp_path / "worktrees" / "triaged-task"

    with kbc.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="Verify a scheduled scan",
            assignee="maintenance-steward",
            workspace_kind="worktree",
            workspace_path=str(missing_workspace),
            triage=True,
        )

        assert kb.get_task(conn, task_id).status == "triage"
        assert not missing_workspace.exists()
        assert kb.complete_task(
            conn,
            task_id,
            summary="Two required scheduled receipts passed.",
        ) is True
        assert kb.get_task(conn, task_id).status == "done"
        run = conn.execute(
            "SELECT metadata FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        assert json.loads(run["metadata"])["repository_changes"] is False


def test_missing_worktree_after_a_run_cannot_be_declared_no_change(
    tmp_path: Path, monkeypatch,
) -> None:
    """A missing checkout after execution remains a hard failure, never a no-op receipt."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    missing_workspace = tmp_path / "worktrees" / "previously-dispatched-task"

    with kbc.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="Task with a missing old worktree",
            assignee="maintenance-steward",
            workspace_kind="worktree",
            workspace_path=str(missing_workspace),
        )
        claimed = kb.claim_task(conn, task_id, claimer="test-worker")
        assert claimed is not None
        assert kb._end_run(
            conn, task_id, outcome="crashed", status="ready", summary="previous attempt",
        ) is not None
        conn.execute("UPDATE tasks SET status = 'triage' WHERE id = ?", (task_id,))
        conn.commit()

        with pytest.raises(CompletionPolicyError, match="worktree no longer exists"):
            kb.complete_task(
                conn,
                task_id,
                summary="No changes claimed despite missing execution workspace.",
                metadata={"repository_changes": False},
            )
        assert kb.get_task(conn, task_id).status == "triage"
