"""Same-second outcomes must use run identity to select the latest attempt."""
import json
import time

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_db_dispatch import check_respawn_guard


@pytest.mark.parametrize("cooldown_outcome,expected_guard", [
    ("rate_limited", "rate_limit_cooldown"),
    ("spawn_failed", "infrastructure_cooldown"),
])
@pytest.mark.parametrize("cooldown_is_newer", [False, True])
def test_tied_run_outcomes_follow_newest_run_and_repeated_reads(
    tmp_path, monkeypatch, cooldown_outcome, expected_guard, cooldown_is_newer,
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(kb, "_resolve_rate_limit_cooldown_seconds", lambda: 86400)
    kb._INITIALIZED_PATHS.clear()
    kb.create_board("run-order")
    with connect_closing(board="run-order") as conn:
        task_id = kb.create_task(conn, title="Synthetic reviewer", board="run-order")
        ended_at = int(time.time())

        def append_run(outcome):
            metadata = {"infrastructure": True} if outcome == "spawn_failed" else {}
            with kb.write_txn(conn):
                cursor = conn.execute(
                    "INSERT INTO task_runs "
                    "(task_id, status, started_at, ended_at, outcome, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (task_id, outcome, ended_at - 1, ended_at, outcome, json.dumps(metadata)),
                )
            return cursor.lastrowid

        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='review' WHERE id=?", (task_id,))
        outcomes = ("crashed", cooldown_outcome) if cooldown_is_newer else (cooldown_outcome, "crashed")
        older_id, newer_id = (append_run(outcome) for outcome in outcomes)
        assert newer_id > older_id
        expected = expected_guard if cooldown_is_newer else None
        # Repeated reads must not revive an older outcome or consume/mutate history.
        assert [check_respawn_guard(conn, task_id) for _ in range(3)] == [expected] * 3
        completed_id = append_run("completed")
        assert completed_id > newer_id
        # Review work intentionally skips the separate ready-lane recent-success guard.
        assert [check_respawn_guard(conn, task_id) for _ in range(3)] == [None] * 3
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_id=?", (task_id,),
        ).fetchone()[0] == 3
        task = kb.get_task(conn, task_id)
        assert task.status == "review" and task.current_run_id is None and task.claim_lock is None
