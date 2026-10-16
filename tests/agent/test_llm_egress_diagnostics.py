"""Content-free diagnostics must agree with the unchanged authorization scan."""
import base64
import pytest
from agent.llm_egress_classifier import _typed_payload_violation_locations
from agent.llm_egress_firewall import (
    EgressBlocked, GeneratedContextSegment, LiteralSegment, TypedOutboundRequest,
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
