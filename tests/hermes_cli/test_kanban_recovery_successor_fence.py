"""A reclaimed attempt cannot account failure against a successor's ownership."""
import json

import pytest

from hermes_cli import kanban_db as kb, kanban_db_dispatch as dispatch
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_db_recovery import _account_crashes


@pytest.mark.parametrize("failure", ["ordinary", "privacy", "provider"])
@pytest.mark.parametrize("successor_finished", [False, True])
def test_reclaimed_failure_cannot_mutate_a_successor(tmp_path, monkeypatch, failure, successor_finished):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.create_board("recovery")
    payload = {}
    if failure == "privacy":
        payload = {"failure_class": "provider_egress_blocked", "privacy_reason": "policy denied"}
    dead = dispatch._DeadWorker("nonzero_exit", 1, "synthetic failure", "crashed", payload,
                                terminal_provider=failure != "ordinary")
    monkeypatch.setattr(dispatch, "_worker_alive", lambda *_: False)
    monkeypatch.setattr(dispatch, "_classify_dead_worker", lambda *args, **kwargs: dead)
    with connect_closing(board="recovery") as conn:
        task_id = kb.create_task(conn, title="Synthetic recovery", assignee="worker", board="recovery")
        original = kb.claim_task(conn, task_id)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET worker_pid=987654321,started_at=0 WHERE id=?", (task_id,))
        sweep = dispatch._reclaim_dead_workers(conn, board="recovery")
        assert sweep.crashed == [task_id]
        # This supported second writer enters after reclaim committed and before accounting.
        with connect_closing(board="recovery") as other:
            successor = kb.claim_task(other, task_id)
            assert successor.current_run_id != original.current_run_id
            if successor_finished:
                assert kb.complete_task(other, task_id, summary="successor finished",
                                        expected_run_id=successor.current_run_id)
        before = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        watermark = conn.execute("SELECT MAX(id) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0]
        for _ in range(2):
            assert _account_crashes(conn, sweep.crash_details, failure_limit=1) == []
            assert dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()) == before
            assert conn.execute("SELECT MAX(id) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0] == watermark
        assert conn.execute("SELECT outcome FROM task_runs WHERE id=?", (original.current_run_id,)).fetchone()[0] == "crashed"


@pytest.mark.parametrize("failure", ["ordinary", "privacy"])
def test_diagnostic_comment_does_not_discard_reclaimed_failure(tmp_path, monkeypatch, failure):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.create_board("comment-recovery")
    payload = {}
    if failure == "privacy":
        payload = {"failure_class": "provider_egress_blocked", "privacy_reason": "policy denied",
                   "egress_reason_codes": ["base64_payload"]}
    dead = dispatch._DeadWorker("nonzero_exit", 1, "synthetic failure", "crashed", payload,
                                terminal_provider=failure == "privacy")
    monkeypatch.setattr(dispatch, "_worker_alive", lambda *_: False)
    monkeypatch.setattr(dispatch, "_classify_dead_worker", lambda *args, **kwargs: dead)
    with connect_closing(board="comment-recovery") as conn:
        task_id = kb.create_task(conn, title="Synthetic diagnostic comment", assignee="worker",
                                 board="comment-recovery")
        claimed = kb.claim_task(conn, task_id)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET worker_pid=987654321,started_at=0 WHERE id=?", (task_id,))
        sweep = dispatch._reclaim_dead_workers(conn, board="comment-recovery")
        assert sweep.crashed == [task_id]
        # A diagnostic comment changes neither the reclaimed phase nor run ownership.
        with connect_closing(board="comment-recovery") as other:
            comment_id = kb.add_comment(other, task_id, "operator", "Synthetic diagnostic observation")
        blocked = _account_crashes(conn, sweep.crash_details, failure_limit=2)
        assert blocked == ([task_id] if failure == "privacy" else [])
        task = kb.get_task(conn, task_id)
        assert task.consecutive_failures == 1
        assert task.status == ("blocked" if failure == "privacy" else "ready")
        assert task.current_run_id is None and task.claim_lock is None
        assert conn.execute("SELECT body FROM task_comments WHERE id=?", (comment_id,)).fetchone()[0] == (
            "Synthetic diagnostic observation")
        assert conn.execute("SELECT outcome FROM task_runs WHERE id=?",
                            (claimed.current_run_id,)).fetchone()[0] == "crashed"
        if failure == "privacy":
            receipt = json.loads(conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='gave_up' ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()[0])
            assert receipt["sticky"] is True and receipt["local_privacy_denial"] is True
            assert receipt["egress_reason_codes"] == ["base64_payload"]
        before = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        for _ in range(2):
            assert _account_crashes(conn, sweep.crash_details, failure_limit=2) == []
            assert dispatch.detect_crashed_workers(conn, board="comment-recovery") == []
            kb.recompute_ready(conn)
            assert dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()) == before
