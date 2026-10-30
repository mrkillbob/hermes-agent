"""Policy recovery preserves bounded typed reasons without consuming the hold."""
import json

import pytest

from hermes_cli import kanban_db as kb, kanban_db_dispatch as dispatch
from hermes_cli.kanban_db_connect import connect_closing


@pytest.mark.parametrize("owner_evidence", ["typed", "last_output"])
def test_exact_owner_denial_keeps_safe_reason_codes_through_repeated_sweeps(tmp_path, monkeypatch, owner_evidence):
    from hermes_cli import kanban_owner_recovery

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.create_board("policy")
    result = {"exit_code": 1, "failure_class": "provider_egress_blocked",
              "egress_reason_codes": ["base64_payload", "PRIVATE_SENTINEL", "private_absolute_path", "base64_payload"],
              "last_output": None}
    if owner_evidence == "last_output":
        # Supported exact-attempt text fallback has no separately typed code array.
        result = {"exit_code": 1,
                  "last_output": "LLM egress blocked: base64_payload,private_absolute_path,base64_payload"}
    monkeypatch.setattr(dispatch, "_worker_alive", lambda *_: False)
    monkeypatch.setattr(kanban_owner_recovery, "managed_run_result", lambda *args, **kwargs: result)
    with connect_closing(board="policy") as conn:
        task_id = kb.create_task(conn, title="Synthetic policy", assignee="worker", board="policy")
        claimed = kb.claim_task(conn, task_id)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET worker_pid=987654321,started_at=0 WHERE id=?", (task_id,))
        assert dispatch.detect_crashed_workers(conn, board="policy") == [task_id]
        task = kb.get_task(conn, task_id)
        assert task.status == "blocked" and task.consecutive_failures == 1
        payloads = [json.loads(row[0]) for row in conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind IN ('needs_attention','gave_up') ORDER BY id",
            (task_id,))]
        assert len(payloads) == 2
        for payload in payloads:
            assert payload["egress_reason_codes"] == ["base64_payload", "private_absolute_path"]
            assert "PRIVATE_SENTINEL" not in json.dumps(payload)
        run = json.loads(conn.execute("SELECT metadata FROM task_runs WHERE id=?",
                                      (claimed.current_run_id,)).fetchone()[0])
        assert run["egress_reason_codes"] == ["base64_payload", "private_absolute_path"]
        assert payloads[-1]["sticky"] is True and payloads[-1]["local_privacy_denial"] is True
        before = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        watermark = conn.execute("SELECT MAX(id) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0]
        for _ in range(2):
            assert dispatch.detect_crashed_workers(conn, board="policy") == []
            kb.recompute_ready(conn)
            assert dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()) == before
            assert conn.execute("SELECT MAX(id) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0] == watermark
