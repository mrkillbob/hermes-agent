"""An orchestrator-lane card whose routing target fails PR write-authority is reported, not stranded.

``route_orchestrator_task`` used to return ``None`` on the authority refusal with no event and no
result bucket, so a ready card sat in ``ready`` forever while ``tail``/``show`` and the stuck-queue
telemetry said nothing about why.
"""
from __future__ import annotations

import json
from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd

_PR_WRITE_BODY = json.dumps({
    "repository": "mrkillbob/hermes-agent", "pr_number": 282,
    "expected_head_sha": "a" * 40, "action": "repair",
})


def _orchestrator_pr_card(tmp_path, monkeypatch, *, authority):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)
    monkeypatch.setattr(kb, "_profile_read_only_status", lambda profile: authority)
    with kbc.connect() as conn:
        # create_task refuses this assignee up front; a legacy row or a later authority change
        # is how such a card reaches the orchestrator lane.
        conn.execute(
            "INSERT INTO tasks (id, title, body, assignee, status, created_by, created_at, priority, "
            "idempotency_key) VALUES ('t_refused', 'GitHub PR feedback: repair review findings', ?, "
            "'task-intake-router', 'ready', 'user', strftime('%s','now'), 0, 'github-pr-feedback:x')",
            (_PR_WRITE_BODY,),
        )
        conn.commit()
    return "t_refused"


def test_unverifiable_write_authority_is_recorded_once_and_visible(tmp_path, monkeypatch):
    tid = _orchestrator_pr_card(tmp_path, monkeypatch, authority=None)
    spawned = []
    with kbc.connect() as conn:
        kbd.dispatch_once(conn, dry_run=True, spawn_fn=lambda *a, **k: spawned.append(a))
        assert "routing_refused" not in [e.kind for e in kb.list_events(conn, tid)]
        results = [
            kbd.dispatch_once(conn, dry_run=False, spawn_fn=lambda *a, **k: spawned.append(a))
            for _ in range(3)
        ]
        events = [e for e in kb.list_events(conn, tid) if e.kind == "routing_refused"]
        task = kb.get_task(conn, tid)
    assert spawned == []
    assert task is not None and task.status == "ready" and task.assignee == "task-intake-router"
    assert all(tid in res.skipped_unassigned for res in results)
    assert len(events) == 1
    assert events[0].payload["assignee"] == "pr-repair-steward"
    assert "cannot verify write authority" in events[0].payload["reason"]


def test_verified_write_profile_routes_the_same_card(tmp_path, monkeypatch):
    tid = _orchestrator_pr_card(tmp_path, monkeypatch, authority=False)
    with kbc.connect() as conn:
        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=lambda *a, **k: None)
        events = [e.kind for e in kb.list_events(conn, tid)]
        task = kb.get_task(conn, tid)
    assert (tid, "pr-repair-steward") in res.routed_to_specialist
    assert task is not None and task.assignee == "pr-repair-steward"
    assert "routing_refused" not in events
