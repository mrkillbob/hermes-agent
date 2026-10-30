"""``hermes pause`` holds kanban dispatch for every entry point, not only the gateway loop.

The CLI, the dashboard nudge and the standalone daemon call ``dispatch_once`` directly; the
emergency stop used to be honored only by the gateway watcher, so a held host could still spawn
workers from one CLI call.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent import estop
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


def _ready_card(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)
    with kbc.connect() as conn:
        return kb.create_task(conn, title="demo", assignee="worker")


def test_paused_dispatch_spawns_and_claims_nothing_and_resumes(tmp_path, monkeypatch):
    tid = _ready_card(tmp_path, monkeypatch)
    spawned = []

    def spawn(task, workspace, board=None):
        spawned.append(task.id)

    estop.engage("hold for acceptance")
    try:
        with kbc.connect() as conn:
            res = kbd.dispatch_once(conn, spawn_fn=spawn)
            task = kb.get_task(conn, tid)
            events = [e.kind for e in kb.list_events(conn, tid)]
            dry = kbd.dispatch_once(conn, dry_run=True, spawn_fn=spawn)
    finally:
        estop.disengage()
    assert res.paused and not res.spawned and spawned == []
    assert task is not None and task.status == "ready" and "claimed" not in events
    # A dry run only reports, so it stays available as a preflight while paused.
    assert not dry.paused and [t for t, _, _ in dry.spawned] == [tid]
    with kbc.connect() as conn:
        resumed = kbd.dispatch_once(conn, spawn_fn=spawn)
    assert not resumed.paused and spawned == [tid]


def test_cli_dispatch_reports_the_pause(tmp_path, monkeypatch, capsys):
    from hermes_cli import kanban_ops

    _ready_card(tmp_path, monkeypatch)
    estop.engage()
    try:
        args = argparse.Namespace(dry_run=False, task_ids=None, max=None, json=True,
                                  failure_limit=kbd.DEFAULT_FAILURE_LIMIT)
        assert kanban_ops._cmd_dispatch(args) == 0
    finally:
        estop.disengage()
    out = json.loads(capsys.readouterr().out)
    assert out["paused"] is True and out["spawned"] == []


def test_paused_standalone_daemon_does_not_report_a_stuck_dispatcher(tmp_path, monkeypatch, capsys):
    from hermes_cli import kanban_ops

    tid = _ready_card(tmp_path, monkeypatch)
    spawned = []

    def drive_ticks(**kwargs):
        with kbc.connect() as conn:
            for _ in range(8):
                result = kbd.dispatch_once(conn, spawn_fn=lambda *args, **kw: spawned.append(args))
                assert result.paused
                kwargs["on_tick"](result)
            task = kb.get_task(conn, tid)
            assert task.status == "ready" and task.claim_lock is None

    monkeypatch.setattr(kbd, "run_daemon", drive_ticks)
    estop.engage("scratch daemon health test")
    try:
        args = argparse.Namespace(force=True, interval=1, max=None, verbose=False, pidfile=None)
        assert kanban_ops._cmd_daemon(args) == 0
    finally:
        estop.disengage()
    assert spawned == []
    assert "dispatcher stuck" not in capsys.readouterr().err


def test_pause_resets_daemon_stuck_window_and_resume_keeps_real_alerts(tmp_path, monkeypatch, capsys):
    from hermes_cli import kanban_ops

    _ready_card(tmp_path, monkeypatch)

    def drive_ticks(**kwargs):
        on_tick = kwargs["on_tick"]
        for _ in range(5):
            on_tick(kbd.DispatchResult())
        on_tick(kbd.DispatchResult(paused=True))
        for _ in range(5):
            on_tick(kbd.DispatchResult())
        assert "dispatcher stuck" not in capsys.readouterr().err
        on_tick(kbd.DispatchResult())

    monkeypatch.setattr(kbd, "run_daemon", drive_ticks)
    args = argparse.Namespace(force=True, interval=1, max=None, verbose=False, pidfile=None)
    assert kanban_ops._cmd_daemon(args) == 0
    assert "dispatcher stuck" in capsys.readouterr().err
