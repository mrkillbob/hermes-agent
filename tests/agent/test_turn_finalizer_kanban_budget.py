"""Regression coverage for Kanban workers that exhaust their turn budget."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from agent.turn_finalizer import _record_kanban_budget_exhausted, finalize_turn
from hermes_cli import kanban_db as kb


def test_budget_exhaustion_parks_task_for_narrower_input(tmp_path, monkeypatch):
    """A completed process that ran out of turns must not enter a respawn loop."""
    import hermes_cli.kanban_db_connect as _hermes_cli_kanban_db_connect
    db_path = tmp_path / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    with _hermes_cli_kanban_db_connect.connect_closing() as conn:
        task_id = kb.create_task(conn, title="needs evidence", assignee="worker")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (task_id,))
        assert kb.claim_task(conn, task_id, claimer="worker") is not None

    _record_kanban_budget_exhausted(task_id, 18, 18, logging.getLogger(__name__))

    with _hermes_cli_kanban_db_connect.connect_closing() as conn:
        task = kb.get_task(conn, task_id)
        events = kb.list_events(conn, task_id)

    assert task is not None
    assert task.status == "blocked"
    assert task.consecutive_failures == 0
    blocked = [event for event in events if event.kind == "blocked"]
    assert blocked
    payload = blocked[-1].payload or {}
    assert payload["kind"] == "needs_input"
    assert "Iteration budget exhausted (18/18)" in payload["reason"]


def test_guardrail_halt_records_kanban_worker_outcome(monkeypatch):
    """A controlled guardrail stop must not look like a missing terminal call."""
    from tests.agent.test_turn_finalizer_final_response_persistence import FakeAgent

    agent = FakeAgent()
    decision = SimpleNamespace(
        tool_name="terminal",
        code="same_tool_failure_halt",
        to_metadata=lambda: {"code": "same_tool_failure_halt"},
    )
    agent._tool_guardrail_halt_decision = decision
    observed = []
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_guardrail")
    monkeypatch.setattr(
        "agent.turn_finalizer._record_kanban_guardrail_halt",
        lambda task_id, actual, _logger: observed.append((task_id, actual)),
    )
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])

    result = finalize_turn(
        agent,
        final_response="Stopped safely.",
        api_call_count=2,
        interrupted=False,
        failed=False,
        messages=[{"role": "user", "content": "work"}],
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="work",
        original_user_message="work",
        _should_review_memory=False,
        _turn_exit_reason="guardrail_halt",
    )

    assert result["guardrail"]["code"] == "same_tool_failure_halt"
    assert observed == [("t_guardrail", decision)]
