"""Host workspace capacity failures must not exhaust a card's retry budget."""

import errno
import json
import os
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli import kanban_db_workspace as workspace


@pytest.fixture
def storage_home(tmp_path, monkeypatch, all_assignees_spawnable):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_KANBAN_RATE_LIMIT_COOLDOWN_SECONDS", "0")
    monkeypatch.setattr(dispatch, "_memory_pressure_level", lambda: "normal")
    kb.init_db()
    return home


@pytest.mark.parametrize("error_number", sorted({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}))
@pytest.mark.parametrize("kind", ["scratch", "worktree"])
@pytest.mark.parametrize("lane", ["ready", "review"])
def test_workspace_capacity_failure_retains_budget_and_partial_work(
    storage_home, tmp_path, monkeypatch, error_number, kind, lane,
):
    partial = tmp_path / "partial-workspace"
    partial.mkdir()
    unique = partial / "unfinished.py"
    unique.write_text("unfinished work\n")

    def no_capacity(*args, **kwargs):
        raise OSError(error_number, os.strerror(error_number), str(partial))

    resolver = "_resolve_worktree_workspace" if kind == "worktree" else "resolve_workspace"
    monkeypatch.setattr(workspace, resolver, no_capacity)
    monkeypatch.setattr(dispatch, "review_dispatch_enabled", lambda: True)

    def must_not_spawn(*args, **kwargs):
        pytest.fail("workspace failure must precede worker dispatch")

    with kbc.connect() as conn:
        tid = kb.create_task(
            conn, title="pending work", assignee="worker", workspace_kind=kind,
            workspace_path=str(partial),
        )
        conn.execute("UPDATE tasks SET consecutive_failures=1, status=? WHERE id=?", (lane, tid))
        conn.commit()
        for _ in range(3):
            result = dispatch.dispatch_once(conn, spawn_fn=must_not_spawn, failure_limit=2)
            assert result.auto_blocked == []
            task = kb.get_task(conn, tid)
            assert task.status == lane
            assert task.consecutive_failures == 1
            assert task.claim_lock is None
            assert task.worker_pid is None
            assert task.current_run_id is None
            assert unique.read_text() == "unfinished work\n"

        runs = conn.execute(
            "SELECT outcome, ended_at, metadata FROM task_runs WHERE task_id=? ORDER BY id",
            (tid,),
        ).fetchall()
        assert len(runs) == 3
        assert all(run["outcome"] == "spawn_failed" for run in runs)
        assert all(run["ended_at"] is not None for run in runs)
        assert all(json.loads(run["metadata"])["infrastructure"] is True for run in runs)

        monkeypatch.setenv("HERMES_KANBAN_RATE_LIMIT_COOLDOWN_SECONDS", "300")
        assert dispatch.check_respawn_guard(conn, tid) == "infrastructure_cooldown"
        dispatch.dispatch_once(conn, spawn_fn=must_not_spawn, failure_limit=2)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_id=?", (tid,),
        ).fetchone()[0] == 3
        conn.execute("UPDATE task_runs SET ended_at=0 WHERE task_id=?", (tid,))
        conn.commit()
        assert dispatch.check_respawn_guard(conn, tid) is None


@pytest.mark.parametrize("failure", [
    OSError(errno.EACCES, "workspace permission denied"),
    RuntimeError("No space left on device"),
    RuntimeError("Disk quota exceeded"),
])
def test_other_workspace_errors_still_spend_retry_budget(storage_home, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(workspace, "resolve_workspace", fail)
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="broken workspace", assignee="worker")
        dispatch.dispatch_once(conn, spawn_fn=lambda *args: None, failure_limit=2)
        assert kb.get_task(conn, tid).consecutive_failures == 1
        run = conn.execute(
            "SELECT metadata FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1", (tid,),
        ).fetchone()
        assert not json.loads(run["metadata"]).get("infrastructure")
