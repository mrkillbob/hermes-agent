"""Slow preparation failures must not account against replacement ownership."""
import errno
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli import kanban_db_workspace as workspace
from hermes_cli.kanban_db_connect import connect_closing


def _snapshot(conn, task_id):
    return {
        table: [tuple(row) for row in conn.execute(
            f"SELECT * FROM {table} WHERE {column}=? ORDER BY rowid", (task_id,),
        )]
        for table, column in (("tasks", "id"), ("task_runs", "task_id"),
                              ("task_events", "task_id"), ("task_comments", "task_id"))
    }


@pytest.mark.parametrize("stage", ["resolve", "persist", "spawn"])
@pytest.mark.parametrize("replacement", ["none", "running", "finished"])
@pytest.mark.parametrize("failure", ["ordinary", "host"])
def test_preparation_failure_respects_attempt_owner(tmp_path, monkeypatch, stage, replacement, failure):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.create_board("preparation")
    observed = {}

    def fail(task, *_args, **_kwargs):
        observed["old_run"] = task.current_run_id
        if replacement != "none":
            with connect_closing(board="preparation") as other:
                assert kb.reclaim_task(other, task.id, reason="synthetic replacement")
                # Reusing the token also proves the run fence matters independently.
                newer = kb.claim_task(other, task.id, claimer=task.claim_lock)
                assert newer.current_run_id != task.current_run_id
                if replacement == "finished":
                    assert kb.complete_task(other, task.id, summary="synthetic accepted result",
                                            expected_run_id=newer.current_run_id)
                observed["before"] = _snapshot(other, task.id)
        if failure == "host":
            if stage == "spawn":
                from tools.process_registry import RestartSafeScopeUnavailable
                raise RestartSafeScopeUnavailable("synthetic host refusal")
            raise OSError(errno.ENOSPC, "synthetic disk refusal")
        raise ValueError("synthetic preparation failure")

    monkeypatch.setattr(workspace, "resolve_dispatch_workspace",
                        fail if stage == "resolve" else lambda *a, **k: (tmp_path / "work", "test"))
    monkeypatch.setattr(workspace, "persist_dispatch_workspace",
                        (lambda conn, task, *a: fail(task)) if stage == "persist" else lambda *a: None)
    monkeypatch.setattr(workspace, "_maybe_emit_scratch_tip", lambda *a: None)
    with connect_closing(board="preparation") as conn:
        task_id = kb.create_task(conn, title="Synthetic preparation", assignee="default", board="preparation")
        result = dispatch.dispatch_once(
            conn, board="preparation", task_ids=[task_id], failure_limit=1,
            spawn_fn=fail if stage == "spawn" else lambda *a, **k: pytest.fail("unexpected spawn"),
        )
        assert result.spawned == []
        if replacement != "none":
            assert _snapshot(conn, task_id) == observed["before"]
            assert result.auto_blocked == []
        else:
            task = kb.get_task(conn, task_id)
            infrastructure = failure == "host" and stage in ("resolve", "spawn")
            assert task.status == ("ready" if infrastructure else "blocked")
            assert task.consecutive_failures == (0 if infrastructure else 1)
            assert task.current_run_id is None and task.claim_lock is None
            assert result.auto_blocked == ([] if infrastructure else [task_id])
            run = conn.execute("SELECT * FROM task_runs WHERE id=?", (observed["old_run"],)).fetchone()
            assert run["status"] == ("spawn_failed" if infrastructure else "gave_up")
            assert run["ended_at"] is not None
