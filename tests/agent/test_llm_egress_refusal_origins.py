"""Refusal-origin receipts retain only fixed structural attribution."""

import base64
from copy import deepcopy
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from agent.llm_egress_firewall import (
    CodexReasoningReplaySegment,
    EgressBlocked,
    OutboundText,
    SanitizedSegment,
    SourceBoundSegment,
    SourcePresentationSegment,
)
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance import SourceProvenanceRegistry


_ENCODED = base64.b64encode(b"synthetic public sample").decode("ascii")
_EXACT_SECRET = "public-fixture-secret-value-3817"
_OPAQUE_REPLAY_SECRET = "gAAAA" + "A" * 10 + "-" + "B" * 10


def _agent(tmp_path):
    return SimpleNamespace(
        provider="custom",
        model="synthetic-model",
        base_url="https://llm.example.test/v1",
        api_mode="chat_completions",
        session_id="synthetic-session",
        _current_turn_id="synthetic-turn",
        _current_api_request_id="synthetic-turn:api:1",
        _llm_egress_policy_digest=sha256(b"synthetic-policy").hexdigest(),
        _llm_egress_state_dir=tmp_path / "egress",
        _source_provenance_registry=SourceProvenanceRegistry(),
    )


def _request(case):
    bodies = {
        "user_task": {"messages": [{"role": "user", "content": _ENCODED}]},
        "user_input_scalar": {"input": _ENCODED},
        "assistant_history": {"messages": [{"role": "assistant", "content": _ENCODED}]},
        "system_or_schema": {"messages": [{"role": "system", "content": _ENCODED}]},
        "tool_schema": {"tools": [{"type": "function", "function": {
            "name": "synthetic_tool", "description": _ENCODED, "parameters": {},
        }}]},
        "tool_result": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "synthetic-call", "type": "function",
                "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
        ]},
        "exact_secret_only": {"messages": [{"role": "user", "content": _EXACT_SECRET}]},
        "split_exact_secret": {"messages": [{"role": "user", "content": OutboundText((
            SanitizedSegment(_EXACT_SECRET[:12]),
            SanitizedSegment(_EXACT_SECRET[12:]),
        ))}]},
        "opaque_reasoning_secret": {"messages": [
            {"role": "assistant", "content": CodexReasoningReplaySegment(_OPAQUE_REPLAY_SECRET)},
            {"role": "user", "content": _ENCODED},
        ]},
        "projected_schema_lane": None,
        "anthropic_tool_result": {"messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "synthetic-native-call", "content": _ENCODED},
        ]}]},
        "anthropic_missing_call_id": {"messages": [{"role": "user", "content": [
            {"type": "tool_result", "content": _ENCODED},
        ]}]},
        "anthropic_nested_spoof": {"messages": [{"role": "user", "content": [
            {"type": "text", "text": {"type": "tool_result", "content": _ENCODED}},
        ]}]},
        "source_bound": {"messages": [{"role": "user", "content": SourceBoundSegment("0" * 64)}]},
        "spoofed_nested_role": {"messages": [{"role": "user", "content": {
            "role": "assistant", "content": _ENCODED,
        }}]},
        "spoofed_nested_tool": {"messages": [{"role": "user", "content": {
            "role": "tool", "content": _ENCODED,
        }}]},
        "other": {"opaque": {"content": _ENCODED}},
    }
    if case == "projected_schema_lane":
        from tools.file_tools import WRITE_FILE_SCHEMA

        bodies[case] = {
            "messages": [{"role": "user", "content": _ENCODED}],
            "tools": [{"type": "function", "function": deepcopy(WRITE_FILE_SCHEMA)}],
        }
    body = deepcopy(bodies[case])
    return {"model": "synthetic-model", **body}


def _blocked_receipt(agent, request):
    with pytest.raises(EgressBlocked) as blocked:
        authorize_agent_sdk_kwargs(agent, request)
    path = agent._llm_egress_state_dir / "llm-egress-receipts.jsonl"
    lines = path.read_bytes().splitlines()
    receipt = json.loads(lines[-1])
    digest = receipt.pop("receipt_sha256")
    previous = receipt["receipt_prev_sha256"]
    assert previous == (sha256(lines[-2]).hexdigest() if len(lines) > 1 else "")
    material = json.dumps(receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    assert digest == sha256(previous.encode("ascii") + material.encode()).hexdigest()
    receipt["receipt_sha256"] = digest
    return blocked.value.decision, receipt


@pytest.mark.parametrize(
    ("case", "origin"),
    [
        ("user_task", "user_task"),
        ("user_input_scalar", "user_task"),
        ("assistant_history", "assistant_history"),
        ("system_or_schema", "system_or_schema"),
        ("tool_schema", "system_or_schema"),
        ("tool_result", "tool_result"),
        ("exact_secret_only", "user_task"),
        ("split_exact_secret", "user_task"),
        ("opaque_reasoning_secret", "user_task"),
        ("projected_schema_lane", "user_task"),
        ("anthropic_tool_result", "tool_result"),
        ("anthropic_missing_call_id", "user_task"),
        ("anthropic_nested_spoof", "user_task"),
        ("source_bound", "source_bound"),
        ("other", "other"),
        ("spoofed_nested_role", "user_task"),
        ("spoofed_nested_tool", "user_task"),
    ],
)
def test_blocked_request_receipt_has_only_fixed_structural_origin(
    tmp_path, monkeypatch, case, origin
):
    agent = _agent(tmp_path)
    if case in {"exact_secret_only", "split_exact_secret"}:
        from agent import llm_egress_runtime as runtime

        monkeypatch.setattr(runtime, "_exact_provider_secret_values", lambda: (_EXACT_SECRET,))
    if case == "opaque_reasoning_secret":
        from agent import llm_egress_runtime as runtime

        monkeypatch.setattr(
            runtime, "_exact_provider_secret_values", lambda: (_OPAQUE_REPLAY_SECRET,)
        )
    if case == "spoofed_nested_tool":
        monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    decision, receipt = _blocked_receipt(agent, _request(case))

    assert decision.allowed is False
    assert receipt["allowed"] is False
    diagnostics = receipt["source_provenance_diagnostics"]
    assert diagnostics["refusal_origins"] == [origin]
    if case in {"exact_secret_only", "split_exact_secret"}:
        assert decision.reason_codes == ("exact_secret_detected",)
        assert _EXACT_SECRET not in json.dumps(diagnostics)
    if case == "opaque_reasoning_secret":
        assert decision.reason_codes == ("base64_payload",)
        assert diagnostics["refusal_origins"] == ["user_task"]
        assert _OPAQUE_REPLAY_SECRET not in json.dumps(diagnostics)
    if case == "projected_schema_lane":
        assert decision.reason_codes == ("base64_payload",)
        assert diagnostics["refusal_origins"] == ["user_task"]
    if case in {"spoofed_nested_role", "spoofed_nested_tool"}:
        assert "assistant" not in json.dumps(diagnostics)
        assert diagnostics["refusal_origins"] == ["user_task"]


def test_origin_attribution_is_request_bound_non_disclosing_and_does_not_change_verdict(
    tmp_path, monkeypatch
):
    agent = _agent(tmp_path)
    first_decision, first = _blocked_receipt(agent, _request("user_task"))
    agent._current_api_request_id = "synthetic-turn:api:2"
    second_decision, second = _blocked_receipt(agent, _request("source_bound"))

    assert first_decision.reason_codes == ("base64_payload",)
    assert second_decision.reason_codes == ("untrusted_provenance",)
    assert first["source_provenance_diagnostics"]["refusal_origins"] == ["user_task"]
    assert second["source_provenance_diagnostics"]["refusal_origins"] == ["source_bound"]
    for receipt in (first, second):
        encoded = json.dumps(receipt["source_provenance_diagnostics"], sort_keys=True)
        for private in ("synthetic-session", "synthetic-turn", "synthetic-call", _ENCODED,
                        "example.test", "source_grant_digest", "content_sha256"):
            assert private not in encoded

    agent._current_api_request_id = "synthetic-turn:api:3"
    allowed, authorization = authorize_agent_sdk_kwargs(agent, {
        "model": "synthetic-model", "messages": [{"role": "user", "content": "ordinary prose"}]
    })
    assert allowed["messages"][0]["content"] == "ordinary prose"
    assert authorization.allowed is True
    path = agent._llm_egress_state_dir / "llm-egress-receipts.jsonl"
    allowed_receipt = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert "refusal_origins" not in allowed_receipt["source_provenance_diagnostics"]

    agent._current_api_request_id = "synthetic-turn:api:4"
    scalar_allowed, scalar_authorization = authorize_agent_sdk_kwargs(agent, {
        "model": "synthetic-model", "input": "ordinary prose"
    })
    assert scalar_allowed["input"] == "ordinary prose"
    assert scalar_authorization.allowed is True
    scalar_receipt = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert "refusal_origins" not in scalar_receipt["source_provenance_diagnostics"]

    from agent import llm_egress_refusal_origins as attribution

    def fail(_value, **_kwargs):
        raise RuntimeError("synthetic diagnostic-only failure")

    monkeypatch.setattr(attribution, "typed_payload_refusal_origins", fail)
    agent._current_api_request_id = "synthetic-turn:api:5"
    denied, receipt = _blocked_receipt(agent, _request("user_task"))
    assert denied.reason_codes == ("base64_payload",)
    assert receipt["source_provenance_diagnostics"]["refusal_origins"] == ["other"]


@pytest.mark.parametrize(
    ("segment", "expected_origin"),
    [
        pytest.param(
            SourcePresentationSegment(
                "opaque-synthetic-grant",
                json.dumps({"content": _EXACT_SECRET}),
                "context_hint_preview_v1:gateway/AGENTS.md",
            ),
            "source_bound",
            id="visible-presentation-match",
        ),
        pytest.param(
            SourcePresentationSegment(
                "opaque-synthetic-grant",
                json.dumps({"content": f"1|{_EXACT_SECRET}"}),
                "read_file_json_v1",
            ),
            "other",
            id="read-presentation-is-not-a-final-secret-scan-input",
        ),
        pytest.param(
            SourceBoundSegment("opaque-synthetic-grant"),
            "other",
            id="unreadable-source-bytes-remain-unknown",
        ),
    ],
)
def test_exact_secret_origin_uses_only_visible_presentation_text(segment, expected_origin):
    from agent.llm_egress_classifier import _typed_payload_violation_locations
    from agent.llm_egress_refusal_origins import typed_payload_refusal_origins

    payload = {"messages": [{
        "role": "user",
        "content": OutboundText((segment,)),
    }]}
    locations = _typed_payload_violation_locations(
        payload, exact_secret_values=(_EXACT_SECRET,)
    )

    if expected_origin == "source_bound":
        assert locations[0][3] == ("exact_secret_detected",)
    else:
        assert locations == ()
    assert typed_payload_refusal_origins(
        payload, exact_secret_values=(_EXACT_SECRET,)
    ) == (expected_origin,)
    assert _EXACT_SECRET not in json.dumps(locations)

