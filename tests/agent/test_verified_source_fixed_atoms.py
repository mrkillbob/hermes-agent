"""Public package vocabulary stays source-only; encoded and private payloads stay blocked."""
import pytest

from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance_tools import attach_trusted_source_provenance_metadata, source_provenance_activation
from agent.tool_dispatch_helpers import make_tool_result_message
from tests.agent.test_llm_egress_runtime import _agent
from tools.file_tools import read_file_tool


def read_source_request(tmp_path, monkeypatch, text):
    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    source = tmp_path / 'source.txt'
    source.write_text(text, newline='')
    agent = _agent(tmp_path / 'egress')
    agent._current_api_request_id = 'turn-1:api:1'
    with source_provenance_activation(agent, 'read_file'):
        content = read_file_tool(str(source), task_id='verified-source-atoms')
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=content)
    message = make_tool_result_message('read_file', content, 'call_source_atoms', source_provenance=metadata)
    agent._current_api_request_id = 'turn-1:api:2'
    return agent, {'model': 'test-model', 'messages': [message]}


@pytest.mark.parametrize('source', [
    'from agent.i18n import t\n',
    'Streams use STANDARD markdown.\n',
    'The gateway SURVIVES the app.\n',
    'SIGTERM reaches the worker.\n',
    'Route ONCE per event.\n',
    'Use the transport that RECEIVED the event.\n',
    'Phases live in `run_*.py`.\n',
])
def test_public_fixed_package_atoms_pass_only_as_verified_source(tmp_path, monkeypatch, source):
    agent, request = read_source_request(tmp_path, monkeypatch, source)
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    assert authorized['messages'][0]['content'] == request['messages'][0]['content']
    assert receipt.decision.source_grant_count == 1
    assert receipt.decision.source_segment_count == 1


@pytest.mark.parametrize('source', [
    'AQID\n', 'c2VjcmV0\n', 'c2VjcmV0LQ==\n', 'QUJD\n',
    'run_\n', 'run_*.txt\n', 'not_run_*.py\n',
    'api_key = sk-123456789012345678901234567890\n',
    'STANDARD workspace /Users/private-person/private-project\n',
])
def test_verified_source_atoms_do_not_exempt_encoded_secret_private_or_other_globs(tmp_path, monkeypatch, source):
    agent, request = read_source_request(tmp_path, monkeypatch, source)
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, request)


@pytest.mark.parametrize('atom', ['i18n', 'STANDARD', 'SURVIVES', 'SIGTERM', 'ONCE', 'RECEIVED', 'run_*.py', 'YWJj'])
def test_the_same_unsourced_atoms_gain_no_remote_context_exemption(tmp_path, atom):
    from agent.llm_egress_firewall import (
        GeneratedContextSegment, LiteralSegment, LLMEgressFirewall, OutboundText, TypedOutboundRequest,
    )
    from agent.source_provenance import DEFAULT_POLICY_DIGEST

    firewall = LLMEgressFirewall(tmp_path / 'egress', policy_digest=DEFAULT_POLICY_DIGEST)
    request = TypedOutboundRequest(
        payload={'messages': [{'role': LiteralSegment('user'), 'content': OutboundText((GeneratedContextSegment(atom),))}]},
        session_id='session', turn_id='turn', request_id='request', policy_digest=DEFAULT_POLICY_DIGEST,
    )
    route = dict(provider='custom', model='test-model', base_url='https://llm.example.test/v1', api_mode='chat_completions')
    with pytest.raises(EgressBlocked) as blocked:
        firewall.preflight(request, route)
    expected_reason = 'static_literal_not_allowed' if atom == 'YWJj' else 'base64_payload'
    assert expected_reason in blocked.value.decision.reason_codes
