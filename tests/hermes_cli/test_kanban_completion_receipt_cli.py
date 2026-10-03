"""CLI coverage for dispatcher-managed repository completion receipts."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_db_workspace as kbw
from hermes_cli.kanban import _cmd_claim, _cmd_complete
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


@pytest.fixture
def manual_worktree(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "cli-test@example.invalid")
    _git(repo, "config", "user.name", "CLI Test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    from hermes_cli.config import _write_user_config
    _write_user_config(home / "config.yaml", {
        "kanban": {"worktree_base_refs": {str(repo.resolve()): base}},
    })
    with kbc.connect_closing() as conn:
        tid = kb.create_task(
            conn, title="Manual claim receipt", workspace_kind="worktree",
            workspace_path=str(repo), branch_name="codex/manual-receipt",
            initial_status="todo", completion_contract="local-only",
        )
        assert kb.promote_task(conn, tid, actor="test", reason="isolated allocation")[0]
    return repo, tid, base


@pytest.mark.parametrize("reuse", [False, True])
def test_manual_claim_persists_actual_branch_and_base_for_clean_completion(
    manual_worktree, reuse,
):
    repo, tid, base = manual_worktree
    branch = "codex/manual-receipt"
    if reuse:
        with kbc.connect_closing() as conn:
            workspace = kbw.resolve_workspace(kb.get_task(conn, tid))
            kbw.set_workspace_path(conn, tid, workspace)
        branch = "codex/actual-manual-branch"
        _git(workspace, "branch", "-m", branch)
    assert _cmd_claim(argparse.Namespace(task_id=tid, ttl=900)) == 0
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        workspace = Path(task.workspace_path)
        assert _git(workspace, "rev-parse", "--show-toplevel") == str(workspace)
        assert task.branch_name == _git(workspace, "branch", "--show-current") == branch
        assert task.workspace_base_sha == base == _git(workspace, "rev-parse", "HEAD")
        assert task.workspace_base_ref == base
    assert _cmd_complete(argparse.Namespace(
        task_ids=[tid], summary="clean allocated worktree", result=None,
        metadata=json.dumps({"repository_changes": False}), force=False,
    )) == 0
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "done"


@pytest.mark.parametrize("evidence", ["missing", "conflicting", "fabricated", "commit"])
@pytest.mark.parametrize("caller", ["manual", "dispatcher"])
def test_manual_claim_cannot_turn_invalid_base_or_real_changes_into_no_change(
    manual_worktree, evidence, caller, capsys,
):
    repo, tid, base = manual_worktree
    branch = "codex/manual-receipt"
    with kbc.connect_closing() as conn:
        workspace = kbw.resolve_workspace(kb.get_task(conn, tid))
        kbw.set_workspace_path(conn, tid, workspace)
        if evidence == "conflicting":
            kbw.set_worktree_base(conn, tid, workspace, branch)
    prefix = f"branch.{branch}.hermes-kanban-base"
    if evidence == "missing":
        _git(workspace, "config", "--unset", prefix + "-sha")
    elif evidence == "fabricated":
        _git(workspace, "config", prefix + "-sha", "0" * 40)
    elif evidence == "conflicting":
        (workspace / "tracked.txt").write_text("actual new base\n", encoding="utf-8")
        _git(workspace, "add", "tracked.txt")
        _git(workspace, "commit", "-m", "new commit")
        _git(workspace, "config", prefix + "-sha", _git(workspace, "rev-parse", "HEAD"))
    if caller == "manual":
        claim_rc = _cmd_claim(argparse.Namespace(task_id=tid, ttl=900))
    else:
        spawned = []
        with kbc.connect_closing() as conn:
            healthy = None
            if evidence != "commit":
                healthy = kb.create_task(
                    conn, title="Continue after invalid assignment", assignee="default",
                    workspace_kind="scratch",
                )
            dispatch = kbd.dispatch_once(
                conn, default_assignee="default",
                spawn_fn=lambda task, _workspace: spawned.append(task.id),
            )
            claim_rc = 0 if kb.get_task(conn, tid).status == "running" else 1
        assert spawned == [healthy or tid]
        assert [entry[0] for entry in dispatch.spawned] == spawned
    if evidence != "commit":
        assert claim_rc == 1
        if caller == "manual":
            assert "base" in capsys.readouterr().err
        with kbc.connect_closing() as conn:
            task = kb.get_task(conn, tid)
            assert task.status == "ready" and task.claim_lock is None
            assert task.workspace_base_sha == (base if evidence == "conflicting" else None)
            run = conn.execute("SELECT ended_at, outcome FROM task_runs WHERE task_id = ?", (tid,)).fetchone()
            assert run["ended_at"] is not None and run["outcome"] == "spawn_failed"
        return
    assert claim_rc == 0
    (workspace / "tracked.txt").write_text("actual repository change\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    _git(workspace, "commit", "-m", "repository changed")
    assert _cmd_complete(argparse.Namespace(
        task_ids=[tid], summary="false no-change declaration", result=None,
        metadata=json.dumps({"repository_changes": False}), force=False,
    )) == 1
    assert "HEAD differs from assigned base" in capsys.readouterr().err
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        assert task.status == "running" and task.workspace_base_sha == base
