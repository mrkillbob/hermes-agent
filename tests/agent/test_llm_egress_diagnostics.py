"""Content-free diagnostics must agree with the unchanged authorization scan."""
import base64
import json
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from agent import llm_egress_request_diagnostics as request_diagnostics
from agent.llm_egress_classifier import _typed_payload_violation_locations
from agent.llm_egress_firewall import (
    AnthropicThinkingReplaySegment, EgressBlocked, GeneratedContextSegment,
    LiteralSegment, TypedOutboundRequest,
    UntrustedProvenanceSegment,
)
from tests.agent.test_llm_egress_firewall import firewall, _route


def _request(content):
    return TypedOutboundRequest(
        payload={'messages': [{'role': LiteralSegment('user'), 'content': content}]},
        session_id='session-1', turn_id='turn-1', request_id='req-1', policy_digest='policy-1')


@pytest.mark.parametrize('text', [
    'github-pr-feedback submit-review --event APPROVE|REQUEST_CHANGES|COMMENT --body TEXT',
    'profile ci-hygiene-fixer follows non-gate-weakening rules; reproduction_command is recorded for HTTP HYGIENE checks.',
])
def test_generated_metadata_diagnostic_matches_allow_decision(tmp_path, text):
    segment = GeneratedContextSegment(text)
    assert firewall(tmp_path).preflight(_request(segment), _route()).allowed
    assert _typed_payload_violation_locations(segment) == ()


def test_real_encoding_and_untrusted_origin_remain_visible_and_denied(tmp_path):
    encoded = base64.b64encode(b'private source that must not leave the host').decode('ascii')
    segment = GeneratedContextSegment(encoded)
    with pytest.raises(EgressBlocked) as denied:
        firewall(tmp_path).preflight(_request(segment), _route())
    assert 'base64_payload' in denied.value.decision.reason_codes
    assert _typed_payload_violation_locations(segment) == (
        ('$', 'GeneratedContextSegment', len(encoded), ('base64_payload',)),)
    marker = UntrustedProvenanceSegment('private-hash')
    with pytest.raises(EgressBlocked) as denied:
        firewall(tmp_path).preflight(_request(marker), _route())
    assert 'untrusted_provenance' in denied.value.decision.reason_codes
    assert _typed_payload_violation_locations(marker) == (
        ('$', 'UntrustedProvenanceSegment', 0, ('untrusted_provenance',)),)


def test_post_tool_final_scan_receipt_correlates_message_without_payload(tmp_path):
    encoded = base64.b64encode(b'synthetic private test payload').decode('ascii')
    request = TypedOutboundRequest(
        payload={"messages": [
            {"role": LiteralSegment("assistant"), "content": AnthropicThinkingReplaySegment(encoded)},
            {"role": LiteralSegment("tool"), "content": GeneratedContextSegment(encoded)},
            {"role": LiteralSegment("user"), "content": LiteralSegment("follow-up")},
        ]},
        session_id="session-1", turn_id="turn-1", request_id="req-1", policy_digest="policy-1",
    )
    authority = SimpleNamespace(profile_id="profile")
    source = object()
    scope = request_diagnostics.DiagnosticScope(
        admission_id="a" * 32, authority_epoch=1, execution_generation=1,
        admission_sequence=1, authority=authority, profile_id="profile",
        logical_session_id="session-1", source=source,
    )
    scope.verify_claimed_turn(authority, "session-1", 1, source, "session-1")
    agent = object()
    scope.bind_turn_agent(agent, "session-1", source)
    token = request_diagnostics.activate(scope)
    try:
        with request_diagnostics.scoped_request(agent, request, nullcontext()):
            with pytest.raises(EgressBlocked) as denied:
                firewall(tmp_path).authorize(request, _route())
        assert denied.value.decision.allowed is False
        assert "base64_payload" in denied.value.decision.reason_codes
        receipt = json.loads((tmp_path / "llm-egress-receipts.jsonl").read_text().splitlines()[-1])
        diagnostic = receipt["refusal_request_v1"]
        assert diagnostic["findings"][0]["carrier"] == "aggregate"
        assert diagnostic["final_scan_attribution"] == [{
            "reason": "base64_payload", "carrier": "tool_result", "message_index": 1,
        }]
        serialized = json.dumps(receipt)
        assert encoded not in serialized
        assert "synthetic private test payload" not in serialized
    finally:
        scope.close()
        request_diagnostics.reset(token)


def test_malformed_message_role_still_fails_closed_without_diagnostic_type_error(tmp_path):
    request = TypedOutboundRequest(
        payload={"messages": [{"role": LiteralSegment([]), "content": LiteralSegment("hello")} ]},
        session_id="session-1", turn_id="turn-1", request_id="req-1", policy_digest="policy-1",
    )

    with pytest.raises(EgressBlocked) as denied:
        firewall(tmp_path).authorize(request, _route())

    assert denied.value.decision.allowed is False
    assert "invalid_literal_segment" in denied.value.decision.reason_codes
