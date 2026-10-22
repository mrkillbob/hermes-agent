"""Calendar syntax is encoding classification, never a source authority grant."""
import base64
import json
from hashlib import sha256

import pytest

from agent.llm_egress_firewall import (
    EgressBlocked, GeneratedContextSegment, OutboundText, UntrustedProvenanceSegment,
    redact_remote_unsafe_text,
)
from tests.agent.test_llm_egress_firewall import _route, _sanitized_request, firewall


@pytest.mark.parametrize('week', ['2026-W42', '2020-W53', '0001-W01', '9999-W52'])
@pytest.mark.parametrize('template', ['{}', 'Weekly review ({})', 'Period: {}.'])
def test_valid_iso_week_keeps_ordinary_text_bytes(tmp_path, week, template):
    text = template.format(week)
    authorized = firewall(tmp_path).authorize(_sanitized_request(text), _route())
    assert json.loads(authorized.payload_bytes)['messages'][0]['content'] == text


@pytest.mark.parametrize('week', ['2026-W42', '2020-W53'])
def test_generated_week_is_preserved_without_source_authority(tmp_path, week):
    assert redact_remote_unsafe_text(week) == week
    request = _sanitized_request(week)
    request.payload['messages'][0]['content'] = GeneratedContextSegment(week)
    authorized = firewall(tmp_path).authorize(request, _route())
    assert json.loads(authorized.payload_bytes)['messages'][0]['content'] == week
    assert authorized.decision.source_grant_count == 0


@pytest.mark.parametrize('text', [
    '2021-W53', '2026-W00', '2026-W54', '0000-W42',
    '2026-W42AA==', 'AQIDAQIDAQID2026-W42', '2026-W42AQIDAQIDAQID',
    'c2VjcmV0', 'AQID',
    base64.b64encode(b'2026-W42').decode('ascii'),
    base64.urlsafe_b64encode(b'private calendar payload\xfb\xff').decode('ascii'),
])
def test_invalid_or_encoded_calendar_like_payload_remains_blocked(tmp_path, text):
    with pytest.raises(EgressBlocked) as blocked:
        firewall(tmp_path).preflight(_sanitized_request(text), _route())
    assert 'base64_payload' in blocked.value.decision.reason_codes


@pytest.mark.parametrize('suffix,reason', [
    (' AQID', 'base64_payload'),
    (' /Users/private-person/private-project', 'private_absolute_path'),
    (' api_key=sk-123456789012345678901234567890', 'secret_detected'),
])
def test_valid_week_never_exempts_adjacent_sensitive_content(tmp_path, suffix, reason):
    with pytest.raises(EgressBlocked) as blocked:
        firewall(tmp_path).preflight(_sanitized_request('2026-W42'+suffix), _route())
    assert reason in blocked.value.decision.reason_codes


def test_valid_week_does_not_replace_untrusted_provenance(tmp_path):
    request = _sanitized_request('2026-W42')
    request.payload['messages'][0]['content'] = OutboundText((
        GeneratedContextSegment('2026-W42'),
        UntrustedProvenanceSegment(sha256(b'unverified source').hexdigest()),
    ))
    with pytest.raises(EgressBlocked) as blocked:
        firewall(tmp_path).preflight(request, _route())
    assert 'untrusted_provenance' in blocked.value.decision.reason_codes


def test_real_verified_file_presentation_preserves_iso_week(tmp_path, monkeypatch):
    from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
    from agent.source_provenance_tools import attach_trusted_source_provenance_metadata, source_provenance_activation
    from agent.tool_dispatch_helpers import make_tool_result_message
    from tests.agent.test_llm_egress_runtime import _agent
    from tools.file_tools import read_file_tool

    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    path = tmp_path / 'calendar.txt'
    path.write_text('Weekly review: 2026-W42\n')
    agent = _agent(tmp_path / 'egress')
    agent._current_api_request_id = 'turn-1:api:1'
    with source_provenance_activation(agent, 'read_file'):
        content = read_file_tool(str(path), task_id='calendar-syntax')
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=content)
    message = make_tool_result_message('read_file', content, 'call_calendar', source_provenance=metadata)
    agent._current_api_request_id = 'turn-1:api:2'
    authorized, receipt = authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':[message]})
    assert authorized['messages'][0]['content'] == content
    assert receipt.decision.source_grant_count == 1
    assert receipt.decision.source_segment_count == 1


@pytest.mark.parametrize('candidate', [
    '2021-W53', '2026-W00', '2026-W54', '0000-W42', '10000-W42',
    '2026-w42', '2026-W4', '2026-W042', '２０２６-W42',
    '2026-W42=', '2026-W42AA==', 'prefix2026-W42', '2026-W42suffix',
    '2026-W42/path', '/2026-W42', '2026-W42\n', ' 2026-W42',
])
def test_iso_week_grammar_rejects_noncanonical_whole_tokens(candidate):
    from agent.egress_encoding_syntax import is_iso_calendar_week

    assert not is_iso_calendar_week(candidate)


def test_calendar_notation_still_matches_configured_exact_secret(tmp_path):
    with pytest.raises(EgressBlocked) as blocked:
        firewall(tmp_path, exact_secret_values=('2026-W42',)).preflight(
            _sanitized_request('2026-W42'), _route(),
        )
    assert 'exact_secret_detected' in blocked.value.decision.reason_codes


@pytest.mark.parametrize('separator', [' ', '\n', '\t', '\r\n', ' \t ', '\n\t'])
@pytest.mark.parametrize('chunks', [('20', '26', '-W', '42'), ('202', '6-W', '42')])
def test_wrapped_calendar_shaped_encoding_remains_denied(tmp_path, separator, chunks):
    text = separator.join(chunks)
    with pytest.raises(EgressBlocked) as blocked:
        firewall(tmp_path).preflight(_sanitized_request(text), _route())
    assert 'base64_payload' in blocked.value.decision.reason_codes


@pytest.mark.parametrize('separator', [' ', '\n', '\t', '\r\n'])
def test_generated_wrapped_calendar_encoding_is_redacted(separator):
    text = separator.join(('20', '26', '-W', '42'))
    assert redact_remote_unsafe_text(text) == '<redacted-base64>'
