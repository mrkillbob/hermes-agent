"""Source envelope identity survives actual system extraction, without index guesses."""
from copy import deepcopy

import pytest

from agent.llm_egress_runtime import _restore_source_provenance_sidecar, authorize_agent_sdk_kwargs
from agent.source_provenance_tools import build_source_provenance_sidecar
from agent.transports.codex import ResponsesApiTransport
from tests.agent.test_responses_source_call_identity import chat_pair, output_items
from tests.agent.test_source_read_result_binding import produce


def built_request(tmp_path, monkeypatch, producer_bound):
    call_id = 'call_' + 'long_identity_' * 8
    agent, messages, paths = produce(tmp_path, monkeypatch, count=1, plain=True, call_ids=[call_id])
    if not producer_bound:
        messages[0]['_source_provenance'].pop('read_result_binding_id')
    chat = [{'role': 'system', 'content': 'Preserve source guidance.'}, *chat_pair(messages[0], paths[0])]
    sidecar = build_source_provenance_sidecar(chat)
    request = ResponsesApiTransport().build_kwargs(model='test-model', messages=chat, tools=[])
    request['_hermes_source_provenance'] = sidecar
    agent.provider = 'openai-codex'
    agent.api_mode = 'codex_responses'
    agent._current_api_request_id = 'turn-1:api:2'
    return agent, request


@pytest.mark.parametrize('producer_bound', [False, True])
def test_actual_transport_preserves_source_through_system_extraction(tmp_path, monkeypatch, producer_bound):
    agent, request = built_request(tmp_path, monkeypatch, producer_bound)
    original = [(item['call_id'], item['output']) for item in output_items(request)]
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    assert receipt.allowed
    assert [(item['call_id'], item['output']) for item in output_items(authorized)] == original


@pytest.mark.parametrize('producer_bound', [False, True])
@pytest.mark.parametrize('field', ['request_id', 'source_grant_digests', 'content_sha256',
                                    'presentation_kind', 'read_result_binding_id', 'tool_call_id'])
def test_transport_cannot_substitute_a_different_source_envelope(tmp_path, monkeypatch, producer_bound, field):
    _, request = built_request(tmp_path, monkeypatch, producer_bound)
    entry = request['_hermes_source_provenance'][0]
    entry[field] = ['unknown'] if field == 'source_grant_digests' else 'unknown'
    restored = _restore_source_provenance_sidecar(request, request['_hermes_source_provenance'])
    assert all('_source_provenance' not in item for item in restored['input'])


@pytest.mark.parametrize('producer_bound', [False, True])
def test_duplicate_exact_envelopes_at_different_indices_remain_ambiguous(tmp_path, monkeypatch, producer_bound):
    _, request = built_request(tmp_path, monkeypatch, producer_bound)
    sidecar = request['_hermes_source_provenance']
    duplicate = deepcopy(sidecar[0])
    duplicate['message_index'] += 20
    sidecar.append(duplicate)
    restored = _restore_source_provenance_sidecar(request, sidecar)
    assert all('_source_provenance' not in item for item in restored['input'])
