"""Dead-worker output receipts, including non-default board routing (#88603 / #46593).

Carried from upstream core tests into a focused module to retain fork source-health boundaries.
"""
import pytest
from tests.hermes_cli.test_kanban_core_functionality import (
    kanban_home as kanban_home,  # noqa: PLC0414
    _drive_protocol_violation,
    _drive_nonzero_crash,
)


_WORKER_LOG_TAIL = (
    "Query: work kanban task\n"
    "╭─ ☤ Hermes ───────────────────╮\n"
    "│ the board protocol requires reassigning this card to orchestrator, but the │\n"
    "│ native kanban_* tools available here have no reassignment operation.       │\n"
    "╰──────────────────────────────╯\n"
    "\nResume this session with:\n  hermes --resume 20260915_000000_abc\n\n"
    "Session:        20260915_000000_abc\nMessages:       3 (1 user, 2 tool calls)\n"
)


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("drive", [_drive_protocol_violation, _drive_nonzero_crash])
def test_dead_worker_reap_surfaces_the_workers_own_last_output(kanban_home, drive):
    """Regression for #88603 / #46593: a worker that explained why it could not comply
    (or printed a provider error) and then exited must have that text on the board and
    on the reap event — with the CLI exit summary trimmed — instead of only the canned label."""
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="handoff", assignee="worker")
        log_path = kb.worker_log_path(tid)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(_WORKER_LOG_TAIL)

        drive(conn, tid, 991100)

        task = kb.get_task(conn, tid)
        assert "no reassignment operation" in (task.last_failure_error or "")
        assert "Resume this session" not in (task.last_failure_error or "")
        assert "Query:" not in (task.last_failure_error or "")
        assert "│" not in (task.last_failure_error or "")
        events = [e for e in kb.list_events(conn, tid) if e.kind in ("protocol_violation", "crashed")]
        assert len(events) == 1
        assert "no reassignment operation" in (events[0].payload or {}).get("worker_output", "")
    finally:
        conn.close()


def test_dead_worker_reap_reads_the_log_of_the_dispatching_board(kanban_home):
    """The reap must read the worker log under the board the tick runs for, not the
    ambient "current" board — otherwise every non-default board silently gets the canned
    message (the #88603 review finding)."""
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_dispatch as kbd
    assert kb.get_current_board() == "default"
    board = "other-board"
    kb.create_board(board)  # explicit creation; connect() must not create boards (#43243)
    conn = kbc.connect(board=board)
    try:
        tid = kb.create_task(conn, title="handoff", assignee="worker")
        log_path = kb.worker_log_path(tid, board=board)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(_WORKER_LOG_TAIL)
        host_prefix = kb._claimer_id().split(":", 1)[0]
        assert kb.claim_task(conn, tid, claimer=f"{host_prefix}:mock") is not None
        kbd._set_worker_pid(conn, tid, 991101)
        kbd._record_worker_exit(991101, 0)
        original_alive = kb._pid_alive
        kb._pid_alive = lambda p: False
        try:
            kbd.detect_crashed_workers(conn, board=board)
        finally:
            kb._pid_alive = original_alive
        task = kb.get_task(conn, tid)
        assert "no reassignment operation" in (task.last_failure_error or "")
    finally:
        conn.close()
