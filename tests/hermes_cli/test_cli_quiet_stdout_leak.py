"""Quiet one-shot chat suppresses callbacks that can write presentation output (#93220)."""

from __future__ import annotations

def test_suppress_status_output_gates_quiet_tool_messages():
    """The executor's fallback [tool]/[done] messages stay silent under -Q."""
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent.quiet_mode = True
    agent.tool_progress_callback = None
    agent.platform = "cli"

    agent.suppress_status_output = False
    assert agent._should_emit_quiet_tool_messages() is True

    agent.suppress_status_output = True
    assert agent._should_emit_quiet_tool_messages() is False
