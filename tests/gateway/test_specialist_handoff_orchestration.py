"""Discord specialist work enters the durable orchestration lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.specialist_handoff import HandoffSource, create_specialist_handoff
from gateway.specialist_routing import (
    RouteKind,
    SpecialistRouteDecision,
    parse_specialist_response,
)
from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_specialist_handoff_creates_goal_mode_triage_root(kanban_home):
    decision = SpecialistRouteDecision(
        kind=RouteKind.SPECIALIST,
        profile="burndown-patch-steward",
        confidence=1.0,
        reason="explicit request",
        title="Narrow Exception Burndown and Patching",
    )
    source = HandoffSource(
        platform="discord",
        chat_id="channel-1",
        chat_type="group",
        user_id="user-1",
        message_id="message-1",
    )

    result = create_specialist_handoff(
        decision=decision,
        source=source,
        request="Audit exception burndown and patch confirmed failures.",
        board="tradingbot-burndown",
    )

    assert result.ok, result.reason
    assert result.task_id
    with kb.connect(board="tradingbot-burndown") as conn:
        task = kb.get_task(conn, result.task_id)
    assert task is not None
    assert task.status == "triage"
    assert task.goal_mode is True
    assert task.goal_max_turns == 12
    assert task.skills == ["tradingbot-worktree-navigation"]


def test_router_accepts_task_orchestrator_for_broad_actionable_work():
    decision = parse_specialist_response(
        '{"kind":"specialist","profile":"task-orchestrator",'
        '"confidence":0.91,"reason":"requires planning across roles",'
        '"title":"Implement the requested workflow"}'
    )

    assert decision.dispatches is True
    assert decision.profile == "task-orchestrator"
