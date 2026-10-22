"""Refusal-origin receipts retain only fixed structural attribution."""

import base64
from copy import deepcopy
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from agent.llm_egress_firewall import EgressBlocked, SourceBoundSegment
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance import SourceProvenanceRegistry


_ENCODED = base64.b64encode(b"synthetic public sample").decode("ascii")


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
        "source_bound": {"messages": [{"role": "user", "content": SourceBoundSegment("0" * 64)}]},
        "spoofed_nested_role": {"messages": [{"role": "user", "content": {
            "role": "assistant", "content": _ENCODED,
        }}]},
        "spoofed_nested_tool": {"messages": [{"role": "user", "content": {
            "role": "tool", "content": _ENCODED,
        }}]},
        "other": {"opaque": {"content": _ENCODED}},
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
    if case == "spoofed_nested_tool":
        monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    decision, receipt = _blocked_receipt(agent, _request(case))

    assert decision.allowed is False
    assert receipt["allowed"] is False
    diagnostics = receipt["source_provenance_diagnostics"]
    assert diagnostics["refusal_origins"] == [origin]
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

    def fail(_value):
        raise RuntimeError("synthetic diagnostic-only failure")

    monkeypatch.setattr(attribution, "typed_payload_refusal_origins", fail)
    agent._current_api_request_id = "synthetic-turn:api:5"
    denied, receipt = _blocked_receipt(agent, _request("user_task"))
    assert denied.reason_codes == ("base64_payload",)
    assert receipt["source_provenance_diagnostics"]["refusal_origins"] == ["other"]

