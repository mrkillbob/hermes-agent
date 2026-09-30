"""Benign local authorization proof; no SDK/network dispatch or private replay."""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import pytest
from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance import SourceProvenanceRegistry
from agent.source_provenance_tools import (
    attach_trusted_source_provenance_metadata, source_provenance_activation,
)
from agent.tool_dispatch_helpers import make_tool_result_message
from tools.file_tools import read_file_tool


def setup_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    source = tmp_path / 'synthetic_head_receipt.txt'
    source.write_text('0123456789abcdef0123456789abcdef01234567\n', encoding='utf-8')
    agent = SimpleNamespace(
        provider='custom', model='test-model', base_url='https://llm.example.test/v1',
        api_mode='chat_completions', session_id='synthetic-session',
        _current_turn_id='synthetic-turn', _current_api_request_id='synthetic-turn:api:1',
        _llm_egress_policy_digest=sha256(b'policy-1').hexdigest(),
        _llm_egress_state_dir=tmp_path/'egress',
        _source_provenance_registry=SourceProvenanceRegistry(),
    )
    with source_provenance_activation(agent, 'read_file'):
        result = read_file_tool(str(source), task_id='benign-receipt-proof')
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=result)
    message = make_tool_result_message('read_file', result, 'synthetic_call_read', source_provenance=metadata)
    kwargs = {'model': 'test-model', 'messages': [message]}
    return source, agent, kwargs


def consume(agent, kwargs, number):
    agent._current_api_request_id = f'synthetic-turn:api:{number}'
    authorized, receipt = authorize_agent_sdk_kwargs(agent, deepcopy(kwargs))
    assert receipt.allowed
    assert receipt.decision.source_grant_count == 1
    assert receipt.decision.source_segment_count == 1
    assert '_source_provenance' not in authorized['messages'][0]
    return authorized


def test_early_cleanup_invalidates_next_consumption(tmp_path, monkeypatch):
    source, agent, kwargs = setup_receipt(tmp_path, monkeypatch)
    consume(agent, kwargs, 2)
    source.unlink()  # Synthetic test-owned receipt only.
    agent._current_api_request_id = 'synthetic-turn:api:3'
    with pytest.raises(EgressBlocked) as error:
        authorize_agent_sdk_kwargs(agent, deepcopy(kwargs))
    assert 'untrusted_provenance' in error.value.decision.reason_codes


def test_keep_until_last_consumption_then_cleanup(tmp_path, monkeypatch):
    source, agent, kwargs = setup_receipt(tmp_path, monkeypatch)
    consume(agent, kwargs, 2)
    consume(agent, kwargs, 3)
    source.unlink()  # Only after final intended consumption, test lifecycle owns cleanup.
    agent._current_api_request_id = 'synthetic-turn:api:4'
    with pytest.raises(EgressBlocked) as error:
        authorize_agent_sdk_kwargs(agent, deepcopy(kwargs))
    assert 'untrusted_provenance' in error.value.decision.reason_codes
