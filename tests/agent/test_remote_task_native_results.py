"""Native Messages tool results keep the same task projection and source proof contract."""
from copy import deepcopy
import json

import pytest

from agent.chat_completion_helpers import _dispatch_provider_request
from agent.llm_egress_firewall import EgressBlocked
from tests.agent.test_remote_task_egress import _agent


@pytest.fixture
def owned_task(monkeypatch):
    import agent.llm_egress_runtime as runtime
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_public')
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    monkeypatch.delenv('HERMES_DELEGATED_CHILD_CONTEXT', raising=False)
    monkeypatch.setattr(runtime, 'egress_enforcement_enabled', lambda: True)
    monkeypatch.setattr(runtime, '_exact_provider_secret_values', lambda: ())


def _native_agent(tmp_path):
    agent = _agent(tmp_path, 'https://api.anthropic.com/v1')
    agent.provider = 'anthropic'
    agent.api_mode = 'anthropic_messages'
    return agent


@pytest.mark.parametrize('blocks', [False, True])
def test_native_kanban_result_projects_bound_task_without_private_history(tmp_path, owned_task, blocks):
    raw = json.dumps({'task': {'status': 'in_progress', 'private_history': 'Household preference: evening ferry.'},
        'private_history': 'Personal diary entry.'})
    content = [{'type': 'text', 'text': raw}] if blocks else raw
    request = {'model': 'test-model', 'messages': [
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'show-1', 'name': 'kanban_show', 'input': {}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'show-1', 'content': content}]}]}
    original = deepcopy(request)
    sent = _dispatch_provider_request(_native_agent(tmp_path), request, lambda body: dict(body))
    replay = json.dumps(sent)
    assert 'kanban_show completed locally.' in replay
    assert 'evening ferry' not in replay and 'Personal diary' not in replay
    assert request == original


@pytest.mark.parametrize('result_id', ['unknown-1', None])
@pytest.mark.parametrize('blocks', [False, True])
def test_native_unbound_result_never_reaches_provider(tmp_path, owned_task, result_id, blocks):
    text = 'Household preference: evening ferry.'
    result = {'type': 'tool_result', 'content': [{'type': 'text', 'text': text}] if blocks else text}
    if result_id is not None:
        result['tool_use_id'] = result_id
    request = {'model': 'test-model', 'messages': [{'role': 'user', 'content': [result]}]}
    sent = []
    with pytest.raises(EgressBlocked):
        _dispatch_provider_request(_native_agent(tmp_path), request, lambda body: sent.append(body))
    assert not sent


@pytest.mark.parametrize('blocks', [False, True])
@pytest.mark.parametrize('tamper', [None, 'content', 'duplicate'])
def test_native_real_file_read_retains_verified_source(tmp_path, monkeypatch, owned_task, blocks, tamper):
    from agent.anthropic_message_convert import convert_messages_to_anthropic
    from agent.source_provenance_tools import (
        attach_trusted_source_provenance_metadata, build_source_provenance_sidecar, source_provenance_activation,
    )
    from agent.tool_dispatch_helpers import make_tool_result_message
    from tools.file_tools import read_file_tool

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    source = workspace / 'source.py'
    source.write_text('first = 1\nsecond = 2\n')
    monkeypatch.chdir(workspace)
    monkeypatch.setenv('TERMINAL_CWD', str(workspace))
    monkeypatch.setenv('HERMES_KANBAN_WORKSPACE', str(workspace))
    agent = _native_agent(tmp_path)
    agent._current_api_request_id = 'turn-1:api:1'
    with source_provenance_activation(agent, 'read_file'):
        raw = read_file_tool('source.py', task_id='native-real-read')
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=raw)
    messages = [
        {'role': 'assistant', 'tool_calls': [{'id': 'read-1', 'type': 'function',
            'function': {'name': 'read_file', 'arguments': '{"path":"source.py"}'}}]},
        make_tool_result_message('read_file', raw, 'read-1', source_provenance=metadata)]
    sidecar = build_source_provenance_sidecar(messages)
    _, native = convert_messages_to_anthropic(messages, model=agent.model, base_url=agent.base_url)
    if blocks:
        result = native[-1]['content'][0]
        result['content'] = [{'type': 'text', 'text': result['content']}]
    if tamper == 'content':
        native[-1]['content'][0]['content'] = 'Private diary entry: evening ferry.'
    elif tamper == 'duplicate':
        native[-1]['content'].append(deepcopy(native[-1]['content'][0]))
    agent._current_api_request_id = 'turn-1:api:2'
    request = {'model': agent.model, 'messages': native, '_hermes_source_provenance': sidecar}
    original = deepcopy(request)
    sent = _dispatch_provider_request(agent, request, lambda body: dict(body))
    replay = json.dumps(sent)
    if tamper is None:
        assert 'first = 1' in replay and 'second = 2' in replay
    else:
        assert 'first = 1' not in replay and 'second = 2' not in replay
        assert 'evening ferry' not in replay
    assert str(workspace) not in replay
    assert '_source_provenance' not in replay
    assert request == original
    assert source.read_text() == 'first = 1\nsecond = 2\n'


@pytest.mark.parametrize('name', ['memory', 'mcp__context_notes', 'tool_describe'])
@pytest.mark.parametrize('blocks', [False, True])
def test_operator_native_admission_cannot_replay_owned_task_private_context(tmp_path, owned_task, name, blocks):
    text = 'Household preference: evening ferry.'
    request = {'model': 'test-model', 'messages': [
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'context-1', 'name': name, 'input': {}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'context-1',
            'content': [{'type': 'text', 'text': text}] if blocks else text}]}]}
    original = deepcopy(request)
    sent = []
    if name == 'memory':
        with pytest.raises(EgressBlocked) as blocked:
            _dispatch_provider_request(_native_agent(tmp_path), request, lambda body: sent.append(dict(body)))
        assert 'untrusted_provenance' in blocked.value.decision.reason_codes
        assert not sent
    else:
        # Read aliases and catalog results already have bounded task projections.
        _dispatch_provider_request(_native_agent(tmp_path), request, lambda body: sent.append(dict(body)))
        assert len(sent) == 1 and 'evening ferry' not in json.dumps(sent)
    assert request == original
