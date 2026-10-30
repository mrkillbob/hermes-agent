"""Receipt origins follow actual owner-bound native replay projections."""

from contextlib import nullcontext
import json

import pytest

from agent.native_metadata_capture import native_metadata_execution_scope
from tests.agent.test_llm_egress_refusal_origins import _ENCODED, _blocked_receipt
from tests.agent.test_native_metadata_capture import _owner
from tests.agent.test_native_metadata_egress import (
    _actual_dispatch_and_commit, _configure, _frames, _initial,
)


def _unsafe_field(body, field, *, before=False):
    if field == "schema":
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "synthetic", "schema": {"description": _ENCODED},
        }}
        if before:
            schema = body.pop("response_format")
            old = dict(body)
            body.clear()
            body.update(response_format=schema, **old)
    else:
        body["messages"].insert(0 if before else len(body["messages"]), {"role": "user", "content": _ENCODED})


@pytest.mark.parametrize(("field", "origin"), [("user", "user_task"), ("schema", "system_or_schema")])
@pytest.mark.parametrize("before", [False, True])
def test_valid_native_replay_does_not_supply_a_false_denial_origin(tmp_path, monkeypatch, field, origin, before):
    from agent import native_metadata_egress, llm_egress_refusal_origins
    validating_during_diagnostics = []
    phase = {"diagnostic": False}
    original_validate = native_metadata_egress.validate_segment
    original_origins = llm_egress_refusal_origins.typed_payload_refusal_origins

    def observe_validation(*args):
        validating_during_diagnostics.append(phase["diagnostic"])
        return original_validate(*args)

    def observe_origins(*args, **kwargs):
        phase["diagnostic"] = True
        try:
            return original_origins(*args, **kwargs)
        finally:
            phase["diagnostic"] = False

    monkeypatch.setattr(native_metadata_egress, "validate_segment", observe_validation)
    monkeypatch.setattr(llm_egress_refusal_origins, "typed_payload_refusal_origins", observe_origins)
    with _owner() as fixture:
        _configure(fixture, tmp_path)
        with native_metadata_execution_scope(fixture.authority, fixture.policy, fixture.source, fixture.agent.session_id):
            _initial(fixture)
            content = _actual_dispatch_and_commit(fixture, monkeypatch)
            fixture.agent._current_api_request_id = "turn-1:api:2"
            body = _frames("chat", fixture.args, content)
            _unsafe_field(body, field, before=before)
            decision, receipt = _blocked_receipt(fixture.agent, body)
            assert decision.allowed is False and "base64_payload" in decision.reason_codes
            diagnostics = receipt["source_provenance_diagnostics"]
            assert diagnostics["refusal_origins"] == [origin]
            serialized = json.dumps(diagnostics)
            assert _ENCODED not in serialized and fixture.request_id not in serialized
            from agent.native_metadata_egress import _ACTIVE
            assert _ACTIVE.get() is None
            live = fixture.authority.sessions[fixture.source.chat_id]
            assert live._native_metadata_admitted[1] == {}
            assert validating_during_diagnostics and not any(validating_during_diagnostics)


@pytest.mark.parametrize("invalid", ["proof", "unbound", "request", "recipient", "owner", "call"])
def test_unadmitted_replay_keeps_its_independent_tool_result_finding(tmp_path, monkeypatch, invalid):
    with _owner() as fixture:
        _configure(fixture, tmp_path)
        with native_metadata_execution_scope(fixture.authority, fixture.policy, fixture.source, fixture.agent.session_id):
            _initial(fixture)
            content = _actual_dispatch_and_commit(fixture, monkeypatch)
            fixture.agent._current_api_request_id = "turn-1:api:2"
            if invalid == "proof":
                content += " altered presentation"
            if invalid == "request":
                fixture.agent._current_api_request_id = "turn-1:api:3"
            if invalid == "recipient":
                fixture.agent.model = "other-offline-model"
            if invalid == "owner":
                fixture.agent.session_id = "other-synthetic-owner"
            body = _frames("chat", fixture.args, content, call_id="other-call" if invalid == "call" else "call-1")
            _unsafe_field(body, "user")
            context = native_metadata_execution_scope(None, None, None, "") if invalid == "unbound" else nullcontext()
            with context:
                decision, receipt = _blocked_receipt(fixture.agent, body)
            assert decision.allowed is False
            assert "base64_payload" in decision.reason_codes
            assert {"tool_result", "user_task"} <= set(receipt["source_provenance_diagnostics"]["refusal_origins"])


@pytest.mark.parametrize(("text", "reason"), [
    (_ENCODED, "base64_payload"),
    ("/Users/synthetic/private/result.txt", "private_absolute_path"),
    ("synthetic-exact-secret-value", "exact_secret_detected"),
])
@pytest.mark.parametrize("segment_cap", [32768, 31])
def test_native_projection_keeps_untrusted_external_result_findings(tmp_path, monkeypatch, text, reason, segment_cap):
    with _owner() as fixture:
        _configure(fixture, tmp_path)
        fixture.agent._llm_egress_max_sanitized_segment_bytes = segment_cap
        with native_metadata_execution_scope(fixture.authority, fixture.policy, fixture.source, fixture.agent.session_id):
            _initial(fixture)
            result = json.loads(json.loads(fixture.result)["result"])
            result["external"] = text
            fixture.result = json.dumps({"result": json.dumps(result)})
            content = _actual_dispatch_and_commit(fixture, monkeypatch)
            fixture.agent._current_api_request_id = "turn-1:api:2"
            if reason == "exact_secret_detected":
                monkeypatch.setattr("agent.llm_egress_runtime._exact_provider_secret_values", lambda: (text,))
            decision, receipt = _blocked_receipt(fixture.agent, _frames("chat", fixture.args, content))
            assert reason in decision.reason_codes
            assert receipt["source_provenance_diagnostics"]["refusal_origins"] == ["tool_result"]


@pytest.mark.parametrize("before", [False, True])
def test_unobserved_native_segment_never_falls_back_to_its_raw_text(tmp_path, before):
    from agent.llm_egress_firewall import LiteralSegment, NativeMetadataSegment, SanitizedSegment
    from agent.source_provenance_diagnostics import native_metadata_scan_inputs
    from tests.agent.test_llm_egress_refusal_origins import _agent, _typed_firewall_result

    unobserved = NativeMetadataSegment(_ENCODED, object())
    user = {"role": LiteralSegment("user"), "content": SanitizedSegment(_ENCODED)}
    assistant = {"role": LiteralSegment("assistant"), "tool_calls": [{
        "id": SanitizedSegment("unobserved-call"), "type": LiteralSegment("function"),
        "function": {"name": SanitizedSegment("synthetic_tool"), "arguments": SanitizedSegment("{}")},
    }]}
    result = {"role": LiteralSegment("tool"), "tool_call_id": SanitizedSegment("unobserved-call"),
              "content": unobserved}
    messages = [user, assistant, result] if before else [assistant, result, user]
    decision, receipt, _ = _typed_firewall_result(_agent(tmp_path), {"messages": messages})
    assert "invalid_native_metadata_reference" in decision.reason_codes
    assert "base64_payload" in decision.reason_codes
    assert receipt["source_provenance_diagnostics"]["refusal_origins"] == ["user_task"]
    assert native_metadata_scan_inputs(unobserved) is None
