"""Refusal-origin receipts retain only fixed structural attribution."""

import base64
from copy import deepcopy
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from agent.llm_egress_firewall import (
    AnthropicThinkingReplaySegment,
    CodexReasoningReplaySegment,
    GeneratedContextKey,
    EgressBlocked,
    GeneratedContextSegment,
    LLMEgressFirewall,
    LiteralSegment,
    OutboundText,
    SanitizedSegment,
    SourceGrant,
    SourceBoundSegment,
    SourcePresentationSegment,
    TypedOutboundRequest,
    ValidatedToolSyntaxSegment,
    source_grant_digest,
    static_literal_sha256,
)
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance import SourceProvenanceRegistry


_ENCODED = base64.b64encode(b"synthetic public sample").decode("ascii")
_EXACT_SECRET = "public-fixture-secret-value-3817"
_OPAQUE_REPLAY_SECRET = "gAAAA" + "A" * 10 + "-" + "B" * 10
_JOINED_BASE64 = base64.b64encode(
    b"synthetic adjacent segment scanner fixture with enough bytes"
).decode("ascii")


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
        "openai_unpaired_tool_result": {"messages": [
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
        ]},
        "openai_wrong_tool_result_id": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "synthetic-call", "type": "function",
                "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "invented-call", "content": _ENCODED},
        ]},
        "openai_result_before_call": {"messages": [
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
            {"role": "assistant", "tool_calls": [{"id": "synthetic-call", "type": "function",
                "function": {"name": "terminal", "arguments": "{}"}}]},
        ]},
        "openai_non_string_call_id": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": 17, "type": "function",
                "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "17", "content": _ENCODED},
        ]},
        "openai_duplicate_tool_call_id": {"messages": [
            {"role": "assistant", "tool_calls": [
                {"id": "synthetic-call", "type": "function", "function": {"name": "terminal", "arguments": "{}"}},
                {"id": "synthetic-call", "type": "function", "function": {"name": "terminal", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
        ]},
        "openai_duplicate_tool_result_id": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "synthetic-call", "type": "function",
                "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
        ]},
        "openai_composite_call_alias_result": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "call_synthetic|fc_synthetic", "type": "function",
                "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_synthetic", "content": _ENCODED},
        ]},
        "openai_ambiguous_call_alias": {"messages": [
            {"role": "assistant", "tool_calls": [
                {"id": "call_synthetic|fc_first", "type": "function",
                    "function": {"name": "terminal", "arguments": "{}"}},
                {"id": "call_synthetic|fc_second", "type": "function",
                    "function": {"name": "terminal", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_synthetic", "content": _ENCODED},
        ]},
        "openai_ambiguous_result_alias": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "call_synthetic|fc_synthetic", "type": "function",
                "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_synthetic", "content": _ENCODED},
            {"role": "tool", "tool_call_id": "fc_synthetic", "content": _ENCODED},
        ]},
        "openai_alias_result_before_call": {"messages": [
            {"role": "tool", "tool_call_id": "call_synthetic", "content": _ENCODED},
            {"role": "assistant", "tool_calls": [{"id": "call_synthetic|fc_synthetic", "type": "function",
                "function": {"name": "terminal", "arguments": "{}"}}]},
        ]},
        "openai_missing_function": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "synthetic-call", "type": "function"}]},
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
        ]},
        "openai_illtyped_function": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "synthetic-call", "type": "function",
                "function": "terminal"}]},
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
        ]},
        "openai_missing_function_name": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "synthetic-call", "type": "function",
                "function": {"arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
        ]},
        "openai_illtyped_arguments": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "synthetic-call", "type": "function",
                "function": {"name": "terminal", "arguments": {}}}]},
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
        ]},
        "openai_missing_arguments": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "synthetic-call", "type": "function",
                "function": {"name": "terminal"}}]},
            {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED},
        ]},
        "responses_tool_result": {"input": [
            {"type": "function_call", "call_id": "synthetic-response-call", "name": "terminal",
                "arguments": "{}"},
            {"type": "function_call_output", "call_id": "synthetic-response-call", "output": _ENCODED},
        ]},
        "responses_assistant_history_call": {"input": [
            {"type": "function_call", "call_id": "synthetic-response-call",
                "name": "terminal", "arguments": _ENCODED},
        ]},
        "responses_unpaired_tool_result": {"input": [
            {"type": "function_call_output", "call_id": "synthetic-response-call", "output": _ENCODED},
        ]},
        "responses_wrong_tool_result_id": {"input": [
            {"type": "function_call", "call_id": "synthetic-response-call", "name": "terminal",
                "arguments": "{}"},
            {"type": "function_call_output", "call_id": "invented-response-call", "output": _ENCODED},
        ]},
        "responses_result_before_call": {"input": [
            {"type": "function_call_output", "call_id": "synthetic-response-call", "output": _ENCODED},
            {"type": "function_call", "call_id": "synthetic-response-call", "name": "terminal",
                "arguments": "{}"},
        ]},
        "responses_empty_call_id": {"input": [
            {"type": "function_call", "call_id": "", "name": "terminal", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "", "output": _ENCODED},
        ]},
        "responses_duplicate_call_id": {"input": [
            {"type": "function_call", "call_id": "synthetic-response-call", "name": "terminal",
                "arguments": "{}"},
            {"type": "function_call", "call_id": "synthetic-response-call", "name": "terminal",
                "arguments": "{}"},
            {"type": "function_call_output", "call_id": "synthetic-response-call", "output": _ENCODED},
        ]},
        "responses_duplicate_result_id": {"input": [
            {"type": "function_call", "call_id": "synthetic-response-call", "name": "terminal",
                "arguments": "{}"},
            {"type": "function_call_output", "call_id": "synthetic-response-call", "output": _ENCODED},
            {"type": "function_call_output", "call_id": "synthetic-response-call", "output": _ENCODED},
        ]},
        "responses_missing_name": {"input": [
            {"type": "function_call", "call_id": "synthetic-response-call", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "synthetic-response-call", "output": _ENCODED},
        ]},
        "responses_illtyped_arguments": {"input": [
            {"type": "function_call", "call_id": "synthetic-response-call", "name": "terminal",
                "arguments": {}},
            {"type": "function_call_output", "call_id": "synthetic-response-call", "output": _ENCODED},
        ]},
        "responses_missing_arguments": {"input": [
            {"type": "function_call", "call_id": "synthetic-response-call", "name": "terminal"},
            {"type": "function_call_output", "call_id": "synthetic-response-call", "output": _ENCODED},
        ]},
        "exact_secret_only": {"messages": [{"role": "user", "content": _EXACT_SECRET}]},
        "split_exact_secret": {"messages": [{"role": "user", "content": OutboundText((
            SanitizedSegment(_EXACT_SECRET[:12]),
            SanitizedSegment(_EXACT_SECRET[12:]),
        ))}]},
        "joined_sanitized_secret": {"messages": [{"role": "user", "content": OutboundText((
            SanitizedSegment("API_K"),
            SanitizedSegment("EY=synthetic-value-3817"),
        ))}]},
        "joined_private_path": {"messages": [{"role": "user", "content": OutboundText((
            SanitizedSegment("/Users"),
            SanitizedSegment("/synthetic/.ssh/id_rsa"),
        ))}]},
        "joined_base64": {"messages": [{"role": "user", "content": OutboundText((
            SanitizedSegment(_JOINED_BASE64[:3]),
            SanitizedSegment(_JOINED_BASE64[3:]),
        ))}]},
        "joined_non_source_secret": {"messages": [{"role": "user", "content": OutboundText((
            SanitizedSegment("API_K"),
            GeneratedContextSegment("EY=synthetic-value-3817"),
        ))}]},
        "joined_non_source_path": {"messages": [{"role": "user", "content": OutboundText((
            GeneratedContextSegment("/Users"),
            GeneratedContextSegment("/synthetic/.ssh/id_rsa"),
        ))}]},
        "opaque_reasoning_secret": {"messages": [
            {"role": "assistant", "content": CodexReasoningReplaySegment(_OPAQUE_REPLAY_SECRET)},
            {"role": "user", "content": _ENCODED},
        ]},
        "projected_schema_lane": None,
        "anthropic_tool_result": {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "synthetic-native-call", "name": "terminal", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "synthetic-native-call", "content": _ENCODED},
            ]},
        ]},
        "anthropic_unpaired_tool_result": {"messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "invented-native-call", "content": _ENCODED},
        ]}]},
        "anthropic_wrong_tool_result_id": {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "synthetic-native-call", "name": "terminal", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "invented-native-call", "content": _ENCODED},
            ]},
        ]},
        "anthropic_result_before_call": {"messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "synthetic-native-call", "content": _ENCODED},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "synthetic-native-call", "name": "terminal", "input": {}},
            ]},
        ]},
        "anthropic_empty_call_id": {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "", "name": "terminal", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "", "content": _ENCODED},
            ]},
        ]},
        "anthropic_duplicate_call_id": {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "synthetic-native-call", "name": "terminal", "input": {}},
                {"type": "tool_use", "id": "synthetic-native-call", "name": "terminal", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "synthetic-native-call", "content": _ENCODED},
            ]},
        ]},
        "anthropic_duplicate_result_id": {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "synthetic-native-call", "name": "terminal", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "synthetic-native-call", "content": _ENCODED},
                {"type": "tool_result", "tool_use_id": "synthetic-native-call", "content": _ENCODED},
            ]},
        ]},
        "anthropic_missing_name": {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "synthetic-native-call", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "synthetic-native-call", "content": _ENCODED},
            ]},
        ]},
        "anthropic_illtyped_input": {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "synthetic-native-call", "name": "terminal", "input": []},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "synthetic-native-call", "content": _ENCODED},
            ]},
        ]},
        "anthropic_missing_input": {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "synthetic-native-call", "name": "terminal"},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "synthetic-native-call", "content": _ENCODED},
            ]},
        ]},
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


def _typed_firewall_result(agent, payload, *, grants=(), exact_secret_values=()):
    """Run the real renderer with explicit typed payloads and a scoped receipt."""
    from agent.llm_egress_classifier import _structural_literal_hashes
    from agent.llm_egress_refusal_origins import refusal_receipt_scope

    def generated_key_hashes(value, found=None):
        if found is None:
            found = set()
        if isinstance(value, LiteralSegment):
            found.add(static_literal_sha256(value.text))
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str):
                    found.add(static_literal_sha256(key))
                if isinstance(key, GeneratedContextKey):
                    found.add(static_literal_sha256(key.text))
                generated_key_hashes(child, found)
        elif isinstance(value, (list, tuple)):
            for child in value:
                generated_key_hashes(child, found)
        return found

    request = TypedOutboundRequest(
        payload=payload,
        session_id=agent.session_id,
        turn_id=agent._current_turn_id,
        request_id=agent._current_api_request_id,
        policy_digest=agent._llm_egress_policy_digest,
    )
    policy = request.policy_digest
    static_hashes = set(_structural_literal_hashes(payload)) | generated_key_hashes(payload)
    firewall = LLMEgressFirewall(
        agent._llm_egress_state_dir,
        policy_digest=policy,
        static_literal_hashes_by_policy={policy: static_hashes},
        exact_secret_values=exact_secret_values,
    )
    registry = agent._source_provenance_registry
    owner = (request.session_id, request.turn_id, request.request_id, policy)
    route = SimpleNamespace(
        provider="custom", model="synthetic-model",
        base_url="https://llm.example.test/v1", api_mode="chat_completions",
    )
    with refusal_receipt_scope(
        agent, registry, owner, payload, exact_secret_values=exact_secret_values
    ):
        try:
            authorization = firewall.authorize(request, route, grants=grants)
        except EgressBlocked as blocked:
            path = agent._llm_egress_state_dir / "llm-egress-receipts.jsonl"
            lines = path.read_bytes().splitlines()
            receipt = json.loads(lines[-1])
            digest = receipt.pop("receipt_sha256")
            previous = receipt["receipt_prev_sha256"]
            assert previous == (sha256(lines[-2]).hexdigest() if len(lines) > 1 else "")
            material = json.dumps(receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            assert digest == sha256(previous.encode("ascii") + material.encode()).hexdigest()
            receipt["receipt_sha256"] = digest
            return blocked.decision, receipt, None
    return authorization.decision, None, authorization


def _synthetic_grant(agent, tmp_path, content):
    path = tmp_path / "synthetic-source.txt"
    path.write_bytes(content)
    return SourceGrant(
        canonical_path=path.resolve(),
        display_path="synthetic-source.txt",
        line_start=1,
        line_end=len(content.splitlines(keepends=True)),
        content_sha256=sha256(content).hexdigest(),
        byte_count=len(content),
        session_id=agent.session_id,
        turn_id=agent._current_turn_id,
        request_id=agent._current_api_request_id,
        policy_digest=agent._llm_egress_policy_digest,
    )


@pytest.mark.parametrize(
    ("lane", "key_kind", "key_text", "value_text", "expected_reason", "expected_origin"),
    [
        pytest.param(lane, key_kind, key_text, "ordinary synthetic value", reason, origin,
                     id=f"{lane}-{key_kind}-{case}")
        for lane, origin in (("input", "user_task"), ("tools", "system_or_schema"))
        for key_kind in ("raw", "generated")
        for case, key_text, reason in (
            ("secret", "API_KEY=synthetic-key-value-3817", "secret_detected"),
            ("private-path", "/Users/synthetic/.ssh/id_rsa", "private_absolute_path"),
            ("exact-secret", _EXACT_SECRET, "exact_secret_detected"),
        )
    ]
    + [
        pytest.param("input", "raw", "API_KEY=synthetic-key-value-3817",
                     "API_KEY=synthetic-value-in-map-3817", "secret_detected", "user_task",
                     id="input-key-and-value-findings-merge")
    ]
    + [
        pytest.param(lane, key_kind, _ENCODED, "ordinary synthetic value", None, None,
                     id=f"{lane}-{key_kind}-base64-key-excluded")
        for lane in ("input", "tools")
        for key_kind in ("raw", "generated")
    ],
)
def test_mapping_key_attribution_matches_final_renderer_scan_inputs(
    tmp_path, lane, key_kind, key_text, value_text, expected_reason, expected_origin
):
    key = GeneratedContextKey(key_text) if key_kind == "generated" else key_text
    payload = {lane: {key: SanitizedSegment(value_text)}}

    exact_values = (_EXACT_SECRET,) if key_text == _EXACT_SECRET else ()
    decision, receipt, authorization = _typed_firewall_result(
        _agent(tmp_path), payload, exact_secret_values=exact_values
    )

    if expected_reason is None:
        assert decision.allowed is True
        assert authorization is not None
        assert receipt is None
        assert decision.reason_codes == ()
    else:
        assert decision.allowed is False
        assert expected_reason in decision.reason_codes
        assert receipt["source_provenance_diagnostics"]["refusal_origins"] == [expected_origin]
        diagnostics = json.dumps(receipt["source_provenance_diagnostics"], sort_keys=True)
        assert key_text not in diagnostics
        assert "base64_payload" not in decision.reason_codes
        if value_text.startswith("API_KEY="):
            from agent.llm_egress_classifier import _typed_payload_violation_locations

            paths = {path for path, *_ in _typed_payload_violation_locations(payload)}
            assert "$.map[0].value.map[0].key" in paths
            assert "$.map[0].value.map[0].value" in paths


@pytest.mark.parametrize(
    ("placement", "content", "expected_reason", "expected_origin"),
    [
        pytest.param(placement, content, reason, "system_or_schema",
                     id=f"{placement}-{label}")
        for placement in ("top-level", "extra-body")
        for label, content, reason in (
            ("secret", "API_KEY=synthetic-schema-secret-3817", "secret_detected"),
            ("private-path", "/Users/synthetic/.ssh/id_rsa", "private_absolute_path"),
            ("base64", _ENCODED, "base64_payload"),
        )
    ]
    + [
        pytest.param("extra-body-tools", _ENCODED, "base64_payload", "system_or_schema",
                     id="sdk-moved-tools-keep-schema-origin"),
        pytest.param("extra-body-functions", _ENCODED, "base64_payload", "system_or_schema",
                     id="sdk-extra-functions-keep-schema-origin"),
        pytest.param("nested-user", "API_KEY=synthetic-nested-secret-3817",
                     "secret_detected", "user_task", id="nested-user-content-is-not-schema"),
        pytest.param("nested-tool", "API_KEY=synthetic-nested-tool-secret-3817",
                     "secret_detected", "tool_result", id="nested-tool-content-is-not-schema"),
    ],
)
def test_response_format_origin_tracks_only_schema_positions(
    tmp_path, placement, content, expected_reason, expected_origin
):
    scanned_content = SanitizedSegment(content)
    response_format = {"type": "json_schema", "json_schema": {
        "name": "synthetic_schema", "schema": {"value": scanned_content}, "strict": False,
    }}
    payload = {
        "top-level": {"response_format": response_format},
        "extra-body": {"extra_body": {"response_format": response_format}},
        "extra-body-tools": {"extra_body": {"tools": [{"type": "function", "function": {
            "name": "synthetic_tool", "parameters": {"schema": scanned_content},
        }}]}},
        "extra-body-functions": {"extra_body": {"functions": [{
            "name": "synthetic_tool", "parameters": {"schema": scanned_content},
        }]}},
        "nested-user": {"messages": [{"role": "user", "content": {
            "response_format": {"json_schema": {"schema": {"value": scanned_content}}},
        }}]},
        "nested-tool": {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "schema-test-call", "type": "function",
                "function": {"name": "synthetic_tool", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "schema-test-call", "content": {
                "response_format": {"json_schema": {"schema": {"value": scanned_content}}},
            }},
        ]},
    }[placement]

    decision, receipt, authorization = _typed_firewall_result(
        _agent(tmp_path), payload,
        exact_secret_values=(_EXACT_SECRET,) if content == _EXACT_SECRET else (),
    )

    assert decision.allowed is False
    assert expected_reason in decision.reason_codes
    assert receipt["source_provenance_diagnostics"]["refusal_origins"] == [expected_origin]
    assert authorization is None
    diagnostics = json.dumps(receipt["source_provenance_diagnostics"], sort_keys=True)
    assert content not in diagnostics


@pytest.mark.parametrize(
    ("case", "segment_kind", "content", "expected_reason", "expected_origin"),
    [
        pytest.param(segment_kind + "-" + case, segment_kind, content, reason, origin,
                     id=f"{segment_kind}-{case}")
        for segment_kind in ("bound", "presentation")
        for case, content, reason in (
            ("secret", b"API_KEY=synthetic-source-secret-3817\n", "secret_detected"),
            ("private-path", b"/Users/synthetic/.ssh/id_rsa\n", "private_absolute_path"),
            ("exact-secret", (_EXACT_SECRET + "\n").encode(), "exact_secret_detected"),
            ("base64", (_ENCODED + "\n").encode(), "base64_payload"),
        )
        for origin in ("source_bound",)
    ]
    + [
        pytest.param("presentation-mismatch", "presentation", b"ordinary source\n", None, "other",
                     id="presentation-mismatch-remains-unknown"),
        pytest.param("unbound", "bound", b"ordinary source\n", None, "other",
                     id="unbound-source-remains-unknown"),
        pytest.param("clean-source", "bound", b"ordinary source\n", None, "allowed",
                     id="clean-verified-source-remains-allowed"),
        pytest.param("mixed-source-user", "bound", b"API_KEY=synthetic-source-secret-3817\n",
                     "secret_detected", "mixed", id="mixed-source-and-user-origins-merge"),
    ],
)
def test_verified_source_scan_attribution_is_request_scoped_and_non_disclosing(
    tmp_path, case, segment_kind, content, expected_reason, expected_origin
):
    agent = _agent(tmp_path)
    grant = _synthetic_grant(agent, tmp_path, content)
    digest = source_grant_digest(grant)
    if segment_kind == "bound":
        segment = SourceBoundSegment(digest)
    else:
        text = json.dumps({"content": "\n".join(
            f"{number}|{line}"
            for number, line in enumerate(content.decode("utf-8").splitlines(), start=1)
        )})
        if case == "presentation-mismatch":
            text = json.dumps({"content": "1|different synthetic source"})
        segment = SourcePresentationSegment(digest, text, "read_file_json_v1")
    payload = {"messages": [{
        "role": LiteralSegment("user"), "content": OutboundText((segment,)),
    }]}
    if case == "mixed-source-user":
        payload["messages"].append({
            "role": LiteralSegment("user"), "content": LiteralSegment(_ENCODED),
        })
    grants = () if case == "unbound" else (grant,)
    exact_values = (_EXACT_SECRET,) if case.endswith("-exact-secret") else ()

    decision, receipt, authorization = _typed_firewall_result(
        agent, payload, grants=grants, exact_secret_values=exact_values
    )

    if expected_origin == "allowed":
        assert decision.allowed is True
        assert authorization is not None
        assert receipt is None
    else:
        assert decision.allowed is False
        expected_origins = (
            ["source_bound", "user_task"] if expected_origin == "mixed" else [expected_origin]
        )
        assert receipt["source_provenance_diagnostics"]["refusal_origins"] == expected_origins
        if expected_reason is not None:
            assert expected_reason in decision.reason_codes
        diagnostics = json.dumps(receipt["source_provenance_diagnostics"], sort_keys=True)
        assert content.decode("utf-8").strip() not in diagnostics
        assert str(grant.canonical_path) not in diagnostics

    if case == "bound-secret":
        # A later owner/request gets only its own typed origin, never the source
        # observation from the preceding verified rendering.
        agent._current_api_request_id = "synthetic-turn:api:2"
        next_payload = {"messages": [{
            "role": LiteralSegment("user"), "content": LiteralSegment(_ENCODED),
        }]}
        next_decision, next_receipt, _ = _typed_firewall_result(agent, next_payload)
        assert "base64_payload" in next_decision.reason_codes
        assert next_receipt["source_provenance_diagnostics"]["refusal_origins"] == ["user_task"]

        from agent.source_provenance_diagnostics import append_receipt_diagnostics
        from agent.llm_egress_refusal_origins import refusal_receipt_scope

        outer_owner = (agent.session_id, agent._current_turn_id,
                       "synthetic-turn:api:3", agent._llm_egress_policy_digest)
        inner_owner = (agent.session_id, agent._current_turn_id,
                       "synthetic-turn:api:4", agent._llm_egress_policy_digest)
        empty_payload = {"messages": []}

        def denied_decision(owner):
            return SimpleNamespace(
                session_id=owner[0], turn_id=owner[1], request_id=owner[2],
                policy_digest=owner[3], allowed=False,
            )

        with refusal_receipt_scope(
            agent, agent._source_provenance_registry, outer_owner, empty_payload
        ):
            with refusal_receipt_scope(
                agent, agent._source_provenance_registry, inner_owner, empty_payload
            ):
                from agent.source_provenance_diagnostics import record_verified_source_scan

                record_verified_source_scan(
                    outer_owner, ("API_KEY=synthetic-nested-value-3817",), (), ()
                )
                inner_receipt = {}
                append_receipt_diagnostics(inner_receipt, denied_decision(inner_owner))
                assert inner_receipt["source_provenance_diagnostics"]["refusal_origins"] == ["other"]
            outer_receipt = {}
            append_receipt_diagnostics(outer_receipt, denied_decision(outer_owner))
            assert outer_receipt["source_provenance_diagnostics"]["refusal_origins"] == ["other"]


@pytest.mark.parametrize(
    ("case", "origin"),
    [
        ("user_task", "user_task"),
        ("user_input_scalar", "user_task"),
        ("assistant_history", "assistant_history"),
        ("system_or_schema", "system_or_schema"),
        ("tool_schema", "system_or_schema"),
        ("tool_result", "tool_result"),
        ("openai_unpaired_tool_result", "other"),
        ("openai_wrong_tool_result_id", "other"),
        ("openai_result_before_call", "other"),
        ("openai_non_string_call_id", "other"),
        ("openai_duplicate_tool_call_id", "other"),
        ("openai_duplicate_tool_result_id", "other"),
        ("openai_composite_call_alias_result", "tool_result"),
        ("openai_ambiguous_call_alias", "other"),
        ("openai_ambiguous_result_alias", "other"),
        ("openai_alias_result_before_call", "other"),
        ("openai_missing_function", "other"),
        ("openai_illtyped_function", "other"),
        ("openai_missing_function_name", "other"),
        ("openai_illtyped_arguments", "other"),
        ("openai_missing_arguments", "other"),
        ("responses_tool_result", "tool_result"),
        ("responses_assistant_history_call", "assistant_history"),
        ("responses_unpaired_tool_result", "user_task"),
        ("responses_wrong_tool_result_id", "user_task"),
        ("responses_result_before_call", "user_task"),
        ("responses_empty_call_id", "user_task"),
        ("responses_duplicate_call_id", "user_task"),
        ("responses_duplicate_result_id", "user_task"),
        ("responses_missing_name", "user_task"),
        ("responses_illtyped_arguments", "user_task"),
        ("responses_missing_arguments", "user_task"),
        ("exact_secret_only", "user_task"),
        ("split_exact_secret", "user_task"),
        ("joined_sanitized_secret", "user_task"),
        ("joined_private_path", "user_task"),
        ("joined_base64", "user_task"),
        ("joined_non_source_secret", "user_task"),
        ("joined_non_source_path", "user_task"),
        ("opaque_reasoning_secret", "user_task"),
        ("projected_schema_lane", "user_task"),
        ("anthropic_tool_result", "tool_result"),
        ("anthropic_unpaired_tool_result", "user_task"),
        ("anthropic_wrong_tool_result_id", "user_task"),
        ("anthropic_result_before_call", "user_task"),
        ("anthropic_empty_call_id", "user_task"),
        ("anthropic_duplicate_call_id", "user_task"),
        ("anthropic_duplicate_result_id", "user_task"),
        ("anthropic_missing_name", "user_task"),
        ("anthropic_illtyped_input", "user_task"),
        ("anthropic_missing_input", "user_task"),
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
    if case in {
        "openai_composite_call_alias_result", "openai_ambiguous_call_alias",
        "openai_ambiguous_result_alias", "openai_alias_result_before_call",
        "responses_assistant_history_call",
    }:
        assert decision.reason_codes == ("base64_payload",)
        encoded_diagnostics = json.dumps(diagnostics, sort_keys=True)
        for private_identifier in ("call_synthetic", "fc_synthetic", "synthetic-response-call"):
            assert private_identifier not in encoded_diagnostics


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
    ("effective_mode", "expected_origin"),
    [
        ("unpaired", "user_task"),
        ("paired", "tool_result"),
        ("null_override", "user_task"),
        ("malformed_override", "user_task"),
    ],
)
def test_responses_refusal_receipt_uses_only_effective_extra_body_input(
    tmp_path, effective_mode, expected_origin
):
    """Receipt origin follows the SDK-effective input and its unique call binding."""
    agent = _agent(tmp_path)
    agent.api_mode = "responses"
    call_id = "synthetic-shadowed-call"
    shadowed_input = [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "synthetic_tool",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": "bounded synthetic output",
        },
    ]
    unsafe_call = {
        "type": "function_call",
        "call_id": call_id,
        "name": "synthetic_tool",
        "arguments": "{}",
    }
    unsafe_result = {
        "type": "function_call_output",
        "call_id": call_id,
        "output": _ENCODED,
    }
    if effective_mode in {"null_override", "malformed_override"}:
        shadowed_input = [unsafe_call, unsafe_result]
        effective_input = None if effective_mode == "null_override" else "malformed"
    elif effective_mode == "paired":
        effective_input = [unsafe_call, unsafe_result]
    else:
        effective_input = [unsafe_result]
    request = {
        "model": "synthetic-model",
        "input": shadowed_input,
        "extra_body": {"input": effective_input},
    }

    decision, receipt = _blocked_receipt(agent, request)

    assert decision.allowed is False
    assert "base64_payload" in decision.reason_codes
    diagnostics = receipt["source_provenance_diagnostics"]
    assert diagnostics["refusal_origins"] == [expected_origin]
    encoded_diagnostics = json.dumps(diagnostics, sort_keys=True)
    assert call_id not in encoded_diagnostics
    assert _ENCODED not in encoded_diagnostics


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("joined_sanitized_secret", "secret_detected"),
        ("joined_private_path", "private_absolute_path"),
        ("joined_base64", "base64_payload"),
        ("joined_non_source_secret", "secret_detected"),
        ("joined_non_source_path", "private_absolute_path"),
    ],
)
def test_joined_final_scan_findings_keep_their_container_origin(
    tmp_path, case, reason
):
    decision, receipt = _blocked_receipt(_agent(tmp_path), _request(case))

    assert decision.allowed is False
    assert reason in decision.reason_codes
    assert receipt["source_provenance_diagnostics"]["refusal_origins"] == ["user_task"]
    diagnostics = json.dumps(receipt["source_provenance_diagnostics"], sort_keys=True)
    for synthetic in ("API_KEY=synthetic-value-3817", "/Users/synthetic/.ssh/id_rsa", _JOINED_BASE64):
        assert synthetic not in diagnostics


def test_joined_scans_do_not_cross_outbound_text_boundaries(tmp_path):
    request = {
        "model": "synthetic-model",
        "messages": [
            {"role": "user", "content": OutboundText((SanitizedSegment("/Users"),))},
            {"role": "user", "content": OutboundText((
                SanitizedSegment("/synthetic/.ssh/id_rsa"),
            ))},
        ],
    }

    allowed, decision = authorize_agent_sdk_kwargs(_agent(tmp_path), request)

    assert allowed["messages"][0]["content"].endswith("/Users")
    assert decision.allowed is True

    from agent.llm_egress_refusal_origins import _joined_scan_segment_reasons

    for boundary in (
        SourceBoundSegment("synthetic-grant-reference"),
        SourcePresentationSegment("synthetic-grant-reference", "", "synthetic-kind"),
    ):
        assert _joined_scan_segment_reasons((
            SanitizedSegment("API_K"),
            boundary,
            SanitizedSegment("EY=synthetic-value-3817"),
        )) == {}


@pytest.mark.parametrize(
    ("segment", "expected_origin"),
    [
        pytest.param(
            SourcePresentationSegment(
                "opaque-synthetic-grant",
                json.dumps({"content": _EXACT_SECRET}),
                "context_hint_preview_v1:gateway/AGENTS.md",
            ),
            "other",
            id="unverified-presentation-match-remains-unknown",
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


@pytest.mark.parametrize(
    ("segments", "exact_secret", "expected_reason", "expected_origin"),
    [
        pytest.param(
            (ValidatedToolSyntaxSegment("bad!", "tool_protocol_identifier"),),
            "bad!",
            "invalid_tool_syntax_segment",
            "other",
            id="invalid-tool-syntax-single-segment-is-not-scanned",
        ),
        pytest.param(
            (
                GeneratedContextSegment("left"),
                ValidatedToolSyntaxSegment("bad!", "tool_protocol_identifier"),
                GeneratedContextSegment("right"),
            ),
            "leftbad!right",
            "invalid_tool_syntax_segment",
            "other",
            id="invalid-tool-syntax-joined-value-is-not-scanned",
        ),
        pytest.param(
            (
                GeneratedContextSegment("left"),
                CodexReasoningReplaySegment("bad!"),
                GeneratedContextSegment("right"),
            ),
            "leftbad!right",
            "invalid_codex_reasoning_replay",
            "other",
            id="invalid-codex-replay-joined-value-is-not-scanned",
        ),
        pytest.param(
            (
                GeneratedContextSegment("left"),
                AnthropicThinkingReplaySegment("bad!"),
                GeneratedContextSegment("right"),
            ),
            "leftbad!right",
            "invalid_anthropic_thinking_replay",
            "other",
            id="invalid-anthropic-replay-joined-value-is-not-scanned",
        ),
        pytest.param(
            (
                GeneratedContextSegment("left"),
                CodexReasoningReplaySegment("gAAAA" + "A" * 20),
                GeneratedContextSegment("right"),
            ),
            "left" + "gAAAA" + "A" * 20 + "right",
            "exact_secret_detected",
            "user_task",
            id="valid-codex-replay-remains-in-joined-scan",
        ),
        pytest.param(
            (
                GeneratedContextSegment("left"),
                AnthropicThinkingReplaySegment("A" * 24),
                GeneratedContextSegment("right"),
            ),
            "left" + "A" * 24 + "right",
            "exact_secret_detected",
            "user_task",
            id="valid-anthropic-replay-remains-in-joined-scan",
        ),
        pytest.param(
            (ValidatedToolSyntaxSegment("synthetic-tool", "tool_protocol_identifier"),),
            "synthetic-tool",
            "exact_secret_detected",
            "user_task",
            id="valid-tool-syntax-remains-in-single-segment-scan",
        ),
    ],
)
def test_exact_secret_attribution_uses_final_rendered_segment_text(
    tmp_path, monkeypatch, segments, exact_secret, expected_reason, expected_origin
):
    from agent import llm_egress_runtime as runtime

    monkeypatch.setattr(runtime, "_exact_provider_secret_values", lambda: (exact_secret,))
    request = {
        "model": "synthetic-model",
        "messages": [{"role": "user", "content": OutboundText(segments)}],
    }
    decision, receipt = _blocked_receipt(_agent(tmp_path), request)

    assert decision.allowed is False
    assert expected_reason in decision.reason_codes
    assert ("exact_secret_detected" in decision.reason_codes) is (expected_origin == "user_task")
    diagnostics = receipt["source_provenance_diagnostics"]
    assert diagnostics["refusal_origins"] == [expected_origin]
    assert exact_secret not in json.dumps(diagnostics)


@pytest.mark.parametrize(
    ("segment", "expected_origin", "expected_shape_violation"),
    [
        pytest.param(
            ValidatedToolSyntaxSegment("/tmp/x", "tool_protocol_identifier"),
            "other",
            False,
            id="invalid-typed-path-is-not-attributed-to-user",
        ),
        pytest.param(
            ValidatedToolSyntaxSegment("bad!", "tool_protocol_identifier"),
            "other",
            False,
            id="invalid-benign-syntax-remains-unknown",
        ),
        pytest.param(
            CodexReasoningReplaySegment("A" * 32),
            "other",
            False,
            id="invalid-codex-replay-is-not-shape-scanned",
        ),
        pytest.param(
            CodexReasoningReplaySegment("gAAAA" + "A" * 20),
            "other",
            False,
            id="valid-codex-replay-remains-opaque-to-shape-scans",
        ),
        pytest.param(
            AnthropicThinkingReplaySegment("A" * 24),
            "other",
            False,
            id="valid-anthropic-replay-remains-opaque-to-shape-scans",
        ),
        pytest.param(
            LiteralSegment("/tmp/x"),
            "user_task",
            True,
            id="scanned-literal-path-remains-attributed",
        ),
        pytest.param(
            SourcePresentationSegment("opaque-synthetic-grant", "/tmp/x", "bad-kind"),
            "other",
            False,
            id="rejected-source-presentation-text-is-not-shape-scanned",
        ),
    ],
)
def test_shape_origin_uses_final_rendered_typed_text(
    segment, expected_origin, expected_shape_violation
):
    from agent.llm_egress_classifier import _typed_payload_violation_locations
    from agent.llm_egress_refusal_origins import typed_payload_refusal_origins

    payload = {"messages": [{"role": "user", "content": OutboundText((segment,))}]}

    locations = _typed_payload_violation_locations(payload)
    assert bool(locations) is expected_shape_violation
    assert typed_payload_refusal_origins(payload) == (expected_origin,)

