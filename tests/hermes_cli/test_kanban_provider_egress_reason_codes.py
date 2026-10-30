"""Provider supervision must recognize every emitted firewall denial."""

from __future__ import annotations

import types

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_provider_errors as provider_errors


@pytest.mark.parametrize("reason", [
    "invalid_anthropic_thinking_replay", "unknown_destination", "grant_binding_mismatch", "source_grant_unbound",
    "invalid_tool_syntax_segment", "non_finite_number", "request_identity_mismatch",
    "sanitized_segment_forbidden", "untyped_request_value",
])
def test_provider_egress_parser_keeps_complete_mixed_denials(tmp_path, monkeypatch, reason):
    log_path = tmp_path / "worker.log"
    monkeypatch.setattr(kb, "worker_log_path", lambda _task_id: log_path)
    reasons = f"private_absolute_path,{reason},missing_request_identity"
    log_path.write_text(f"Initializing agent...\nLLM egress blocked: {reasons}\n")
    expected = f"provider egress blocked: LLM egress blocked: {reasons}"
    assert provider_errors._provider_egress_error_text("task") == expected
    assert provider_errors._provider_terminal_error_text("task") == (expected, "provider_egress_blocked")

    log_path.write_text(f"LLM egress blocked: {reasons},invented_denial\n")
    assert provider_errors._provider_egress_error_text("task") is None
    assert provider_errors._provider_terminal_error_text("task") is None


def test_provider_egress_parser_accepts_real_unknown_route_denial(tmp_path, monkeypatch):
    from agent.llm_egress_firewall import EgressBlocked, LLMEgressFirewall

    with pytest.raises(EgressBlocked) as exc_info:
        LLMEgressFirewall(tmp_path / "egress").preflight(
            {"messages": [{"role": "user", "content": "Review carefully."}]},
            types.SimpleNamespace(provider="nous", model="test-model", base_url=""),
        )
    assert "unknown_destination" in exc_info.value.decision.reason_codes
    log_path = tmp_path / "worker.log"
    log_path.write_text(f"Initializing agent...\n{exc_info.value}\n")
    monkeypatch.setattr(kb, "worker_log_path", lambda _task_id: log_path)
    expected = f"provider egress blocked: {exc_info.value}"
    assert provider_errors._provider_egress_error_text("task") == expected
    assert provider_errors._provider_terminal_error_text("task") == (expected, "provider_egress_blocked")
