"""A full worker-log filesystem is a host refusal before any worker launches."""
import errno
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("lane", ["ready", "review"])
@pytest.mark.parametrize("error_number", sorted({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}))
def test_worker_log_capacity_refusal_preserves_budget(tmp_path, monkeypatch, lane, error_number):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_dispatch as dispatch
    from hermes_cli.kanban_db_connect import connect_closing

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_RATE_LIMIT_COOLDOWN_SECONDS", "0")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "_memory_pressure_level", lambda: "normal")
    monkeypatch.setattr(dispatch, "review_dispatch_enabled", lambda: True)

    def full_log(*args, **kwargs):
        raise OSError(error_number, "synthetic log capacity refusal")

    monkeypatch.setattr(dispatch, "_open_worker_log", full_log)
    with connect_closing() as conn:
        task_id = kb.create_task(conn, title="Synthetic log readiness", assignee="default")
        conn.execute("UPDATE tasks SET status=?, consecutive_failures=1 WHERE id=?", (lane, task_id))
        conn.commit()
        for _ in range(2):
            result = dispatch.dispatch_once(conn, task_ids=[task_id], failure_limit=2)
            task = kb.get_task(conn, task_id)
            assert "synthetic log capacity refusal" in task.last_failure_error
            assert result.spawned == [] and result.auto_blocked == []
            assert task.status == lane and task.consecutive_failures == 1
            assert task.current_run_id is None and task.claim_lock is None and task.worker_pid is None
        runs = conn.execute("SELECT status,metadata FROM task_runs WHERE task_id=?", (task_id,)).fetchall()
        assert len(runs) == 2
        assert all(row["status"] == "spawn_failed" for row in runs)
        assert all(json.loads(row["metadata"])["infrastructure"] is True for row in runs)
        monkeypatch.setenv("HERMES_KANBAN_RATE_LIMIT_COOLDOWN_SECONDS", "300")
        assert dispatch.check_respawn_guard(conn, task_id) == "infrastructure_cooldown"


@pytest.mark.parametrize("failure", [OSError(errno.EACCES, "synthetic permission refusal"),
                                     RuntimeError("No space left on device")])
def test_other_log_failures_still_consume_budget(tmp_path, monkeypatch, failure):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_dispatch as dispatch
    from hermes_cli.kanban_db_connect import connect_closing

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "_memory_pressure_level", lambda: "normal")

    def fail_log(*args, **kwargs):
        raise failure

    monkeypatch.setattr(dispatch, "_open_worker_log", fail_log)
    with connect_closing() as conn:
        task_id = kb.create_task(conn, title="Synthetic log failure", assignee="default")
        result = dispatch.dispatch_once(conn, task_ids=[task_id], failure_limit=1)
        task = kb.get_task(conn, task_id)
        assert str(failure) in task.last_failure_error
        assert task.status == "blocked" and task.consecutive_failures == 1
        assert result.auto_blocked == [task_id]
        run = conn.execute("SELECT metadata FROM task_runs WHERE task_id=?", (task_id,)).fetchone()
        assert not json.loads(run["metadata"]).get("infrastructure")


@pytest.mark.parametrize("stage", ["callback", "post_launch"])
def test_plain_capacity_error_has_no_prelaunch_stage_proof(tmp_path, monkeypatch, stage):
    import subprocess
    import sys
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_dispatch as dispatch
    from hermes_cli.kanban_db_connect import connect_closing

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "_memory_pressure_level", lambda: "normal")
    children = []

    def callback(*args, **kwargs):
        if stage == "callback":
            raise OSError(errno.ENOSPC, "synthetic callback capacity error")
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read()"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        children.append(child)
        return child.pid

    def after_launch(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic post-launch capacity error")

    if stage == "post_launch":
        monkeypatch.setattr(kb, "_fire_worker_spawned_hook", after_launch)
    try:
        with connect_closing() as conn:
            task_id = kb.create_task(conn, title="Synthetic uncertain capacity", assignee="default")
            result = dispatch.dispatch_once(conn, task_ids=[task_id], failure_limit=1, spawn_fn=callback)
            task = kb.get_task(conn, task_id)
            assert task.status == "blocked" and task.consecutive_failures == 1
            assert result.auto_blocked == [task_id]
            run = conn.execute("SELECT metadata FROM task_runs WHERE task_id=?", (task_id,)).fetchone()
            assert not json.loads(run["metadata"]).get("infrastructure")
            assert "capacity error" in task.last_failure_error
    finally:
        for child in children:
            child.stdin.close()
            assert child.wait(timeout=5) == 0
