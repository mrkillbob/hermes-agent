"""Owned-task privacy follows the sending route, including auxiliary and native APIs."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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


@pytest.mark.parametrize('provider', ['nvidia', 'custom:unlisted'])
@pytest.mark.parametrize('kind', ['sync', 'async', 'stream'])
def test_auxiliary_actual_remote_route_blocks_private_payload(owned_task, provider, kind):
    from agent import auxiliary_client as aux
    sent = []
    client = SimpleNamespace(base_url='https://remote.example.test/v1',
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: sent.append(kw))))
    request = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'SECRET_TOKEN=private-value'}]}
    original = deepcopy(request)
    with pytest.raises(EgressBlocked):
        if kind == 'async':
            async def send(body):
                sent.append(body)
            asyncio.run(aux._relay_async_completion(client, request, provider=provider, create=send))
        elif kind == 'sync':
            aux._relay_sync_completion(client, request, provider=provider, create=lambda body: sent.append(body))
        else:
            aux._relay_sync_stream(client, request, provider=provider)
    assert not sent
    assert request == original


def test_auxiliary_unknown_client_endpoint_cannot_borrow_local_main_route(owned_task):
    from agent import auxiliary_client as aux
    token = aux._RUNTIME_MAIN_CONTEXT.set({'base_url': 'http://127.0.0.1:8000/v1'})
    try:
        with pytest.raises(EgressBlocked) as blocked:
            aux._dispatch_auxiliary_request(SimpleNamespace(), {'model': 'test-model',
                'messages': [{'role': 'user', 'content': 'Public request.'}]},
                lambda body: pytest.fail('Unknown destination reached provider'),
                provider='nvidia', model='test-model', api_mode='chat_completions')
        assert 'unsupported_remote_task_auxiliary' in blocked.value.decision.reason_codes
    finally:
        aux._RUNTIME_MAIN_CONTEXT.reset(token)


@pytest.mark.parametrize('kind', ['nonstream', 'stream', 'iam_fallback'])
@pytest.mark.parametrize('endpoint', ['https://bedrock-runtime.us-east-1.amazonaws.com', 'http://127.0.0.1:8000/v1'])
def test_owned_remote_bedrock_has_no_unauthorized_send(tmp_path, monkeypatch, owned_task, kind, endpoint):
    from agent import chat_completion_helpers as helpers
    from agent import bedrock_adapter
    agent = _agent(tmp_path, endpoint)
    agent.provider = 'bedrock'
    agent.api_mode = 'bedrock_converse'
    agent._safe_print = lambda *args: None
    client = Mock()
    monkeypatch.setattr(bedrock_adapter, '_get_bedrock_runtime_client', lambda region: client)
    body = {'modelId': 'test-model', 'messages': [{'role': 'user', 'content': [{'text': 'Public request.'}]}]}
    original = deepcopy(body)
    with pytest.raises(EgressBlocked) as blocked:
        if kind == 'nonstream':
            helpers._dispatch_nonstreaming_api_request(agent, body, make_client=Mock())
        else:
            stream = object.__new__(helpers._BedrockStream)
            stream.agent = agent
            if kind == 'stream':
                stream._open_stream(body)
            else:
                stream._fall_back_to_converse(client, body, RuntimeError('AccessDenied'))
    assert 'unsupported_remote_task_transport' in blocked.value.decision.reason_codes
    client.converse.assert_not_called()
    client.converse_stream.assert_not_called()
    assert body == original


@pytest.mark.parametrize('mode', ['local', None])
def test_cached_ambient_prompt_is_not_remote_public_context(tmp_path, owned_task, mode):
    from agent.chat_completion_helpers import _dispatch_provider_request
    agent = _agent(tmp_path)
    agent._cached_system_prompt = 'Private household preference: take the evening ferry.'
    if mode is not None:
        agent._remote_task_context_mode = mode
    sent = []
    with pytest.raises(EgressBlocked) as blocked:
        _dispatch_provider_request(agent, {'model': 'test-model', 'messages': [
            {'role': 'system', 'content': agent._cached_system_prompt},
            {'role': 'user', 'content': 'Public request.'}]}, lambda body: sent.append(body))
    assert 'unverified_remote_task_context' in blocked.value.decision.reason_codes
    assert not sent
    assert agent._cached_system_prompt == 'Private household preference: take the evening ferry.'


def test_public_cached_context_and_local_route_keep_working(tmp_path, owned_task):
    from agent.chat_completion_helpers import _dispatch_provider_request
    agent = _agent(tmp_path)
    agent._cached_system_prompt = 'Public task instructions.'
    agent._remote_task_context_mode = 'public'
    agent._remote_task_public_prompt = agent._cached_system_prompt
    request = {'model': 'test-model', 'messages': [{'role': 'system', 'content': agent._cached_system_prompt}]}
    assert _dispatch_provider_request(agent, request, lambda body: dict(body)) == request
    agent.base_url = 'http://127.0.0.1:8000/v1'
    agent._remote_task_context_mode = 'local'
    assert _dispatch_provider_request(agent, request, lambda body: body) is request


@pytest.mark.parametrize('remote', [True, False])
@pytest.mark.parametrize('explicit', [True, False])
def test_managed_wire_keeps_explicit_skills_without_ambient_personality(tmp_path, owned_task, remote, explicit):
    from agent.turn_context import build_api_messages
    agent = _agent(tmp_path, 'https://remote.example.test/v1' if remote else 'http://127.0.0.1:8000/v1')
    agent._current_turn_timestamp = 1.0
    agent.ephemeral_system_prompt = 'Private household preference: evening ferry.\n\nExplicit launch skill: run checks.'
    if explicit:
        agent._remote_task_ephemeral_system_prompt = 'Explicit launch skill: run checks.'
    agent._copy_reasoning_content_for_api = lambda *_: None
    agent._should_sanitize_tool_calls = lambda: False
    original = agent.ephemeral_system_prompt
    messages = [{'role': 'user', 'content': 'Public request.'}]
    before = deepcopy(messages)
    wire, system = build_api_messages(agent, messages, current_turn_user_idx=0,
        ext_prefetch_cache='', plugin_user_context='', moa_config=None,
        active_system_prompt='Public task instructions.')
    assert ('Explicit launch skill: run checks.' in system) is (explicit or not remote)
    assert ('evening ferry' in system) is not remote
    assert wire[0]['content'] == system
    assert agent.ephemeral_system_prompt == original
    assert messages == before


def test_production_restore_does_not_inherit_public_build_marker(tmp_path, owned_task):
    from agent.conversation_loop import _restore_or_build_system_prompt
    from agent.chat_completion_helpers import _dispatch_provider_request
    from tests.agent.test_system_prompt_restore import _make_agent
    from unittest.mock import MagicMock

    stored = 'Private household preference: evening ferry.'
    db = MagicMock()
    db.get_session.return_value = {'system_prompt': stored}
    agent = _make_agent(session_db=db)
    agent.base_url = 'https://remote.example.test/v1'
    agent.api_mode = 'chat_completions'
    agent._remote_task_context_mode = 'public'
    agent._llm_egress_state_dir = tmp_path / 'receipts'
    agent._llm_egress_policy_digest = _agent(tmp_path)._llm_egress_policy_digest
    _restore_or_build_system_prompt(agent, None, [{'role': 'user', 'content': 'Previous request.'}])
    with pytest.raises(EgressBlocked) as blocked:
        _dispatch_provider_request(agent, {'model': 'test-model', 'messages': [
            {'role': 'system', 'content': agent._cached_system_prompt}]},
            lambda body: pytest.fail('Restored prompt reached provider'))
    assert 'unverified_remote_task_history' in blocked.value.decision.reason_codes
    assert agent._cached_system_prompt == stored
    agent._build_system_prompt.assert_not_called()


@pytest.mark.parametrize('async_client', [False, True])
def test_auxiliary_native_bedrock_is_rejected_before_adapter(tmp_path, owned_task, async_client):
    from agent import auxiliary_client as aux
    client = aux.BedrockAuxiliaryClient('us-east-1', 'test-model')
    if async_client:
        client = aux.AsyncBedrockAuxiliaryClient(client)
    with pytest.raises(EgressBlocked) as blocked:
        aux._auxiliary_egress_binding(client, provider='bedrock', model='test-model', api_mode='chat_completions')
    assert 'unsupported_remote_task_transport' in blocked.value.decision.reason_codes


@pytest.mark.parametrize('scope', ['remote_task', 'local_task', 'operator'])
@pytest.mark.parametrize('path', ['summary', 'failover', 'codex'])
def test_alternate_system_composition_preserves_only_intended_remote_additions(tmp_path, monkeypatch, owned_task, scope, path):
    agent = _agent(tmp_path)
    if scope == 'local_task':
        agent.base_url = 'http://127.0.0.1:8000/v1'
    elif scope == 'operator':
        monkeypatch.delenv('HERMES_KANBAN_TASK')
    agent._cached_system_prompt = 'Public task instructions.'
    agent.ephemeral_system_prompt = 'Private household preference: evening ferry.\n\nExplicit launch skill: run checks.'
    agent._remote_task_ephemeral_system_prompt = 'Explicit launch skill: run checks.'
    original = agent.ephemeral_system_prompt
    if path == 'summary':
        from agent.chat_completion_helpers import _iteration_summary_api_messages
        agent._should_sanitize_tool_calls = lambda: False
        agent._image_rejecting_models = set()
        agent.prefill_messages = None
        agent._sanitize_api_messages = lambda messages: messages
        agent._drop_thinking_only_and_merge_users = lambda messages: messages
        system = _iteration_summary_api_messages(agent, [])[0]['content']
    elif path == 'failover':
        from agent.conversation_loop import _sync_failover_system_message
        messages = [{'role': 'system', 'content': 'Previous public instructions.'}]
        _sync_failover_system_message(agent, messages, agent._cached_system_prompt)
        system = messages[0]['content']
    else:
        from agent.codex_runtime import _codex_developer_instructions
        system = _codex_developer_instructions(agent)
    assert 'Explicit launch skill: run checks.' in system
    assert ('evening ferry' in system) is (scope != 'remote_task')
    assert agent.ephemeral_system_prompt == original


@pytest.mark.parametrize('remote', [True, False])
@pytest.mark.parametrize('source', ['hook', 'recall'])
def test_ambient_turn_context_is_not_loaded_for_owned_remote_tasks(tmp_path, monkeypatch, owned_task, remote, source):
    from agent.turn_context import _collect_pre_llm_call_context, _memory_turn_start_and_prefetch
    agent = _agent(tmp_path, 'https://remote.example.test/v1' if remote else 'http://127.0.0.1:8000/v1')
    ambient = 'Private household preference: evening ferry.'
    if source == 'hook':
        hook = Mock(return_value=[{'context': ambient}])
        monkeypatch.setattr('hermes_cli.lifecycle.invoke_hook', hook)
        result = _collect_pre_llm_call_context(agent, effective_task_id='t_public', turn_id='turn-1',
            original_user_message='Public task request', messages=[], conversation_history=[])
        called = hook.called
    else:
        agent._memory_manager = Mock()
        agent._memory_manager.prefetch_all.return_value = ambient
        agent._user_turn_count = 1
        agent._emit_status = lambda value: None
        result = _memory_turn_start_and_prefetch(agent, 'Please review the public task evidence.')
        called = agent._memory_manager.prefetch_all.called
    assert (ambient in result) is not remote
    assert called is not remote


@pytest.mark.parametrize('endpoint', ['https://remote.example.test/v1', 'http://127.0.0.1:8000/v1'])
def test_codex_app_server_rejected_before_start_or_resume(tmp_path, monkeypatch, owned_task, endpoint):
    from agent import codex_runtime
    agent = _agent(tmp_path, endpoint)
    agent.api_mode = 'codex_app_server'
    ensure = Mock(side_effect=AssertionError('Session start/resume reached'))
    monkeypatch.setattr(codex_runtime, '_ensure_codex_session', ensure)
    with pytest.raises(EgressBlocked) as blocked:
        codex_runtime.run_codex_app_server_turn(agent, user_message='Public request.',
            original_user_message='Public request.', messages=[], effective_task_id='t_public')
    assert 'unsupported_remote_task_transport' in blocked.value.decision.reason_codes
    ensure.assert_not_called()


@pytest.mark.parametrize('remote', [True, False])
def test_profile_prefills_are_absent_from_remote_assembled_request(tmp_path, monkeypatch, owned_task, remote):
    import logging
    from agent.turn_request_assembly import assemble_api_request
    from tests.agent.test_anthropic_preserved_thinking_replay import _patch_assembly_loop
    _patch_assembly_loop(monkeypatch)
    agent = _agent(tmp_path, 'https://remote.example.test/v1' if remote else 'http://127.0.0.1:8000/v1')
    agent._current_turn_timestamp = 1.0
    agent.ephemeral_system_prompt = None
    agent._copy_reasoning_content_for_api = lambda *_: None
    agent._should_sanitize_tool_calls = lambda: False
    agent._sanitize_api_messages = lambda messages: messages
    agent._drop_thinking_only_and_merge_users = lambda messages, **kwargs: messages
    agent.tools = []
    agent._use_prompt_caching = False
    agent.context_compressor = None
    agent.prefill_messages = [{'role': 'user', 'content': 'Private profile prefill: evening ferry.'}]
    original = deepcopy(agent.prefill_messages)
    assembled = assemble_api_request(agent, messages=[{'role': 'user', 'content': 'Public request.'}],
        current_turn_user_idx=0, _ext_prefetch_cache='', _plugin_user_context='', moa_config=None,
        active_system_prompt='Public task instructions.', original_user_message='Public request.',
        pending_moa_prepared_request=None, request_logger=logging.getLogger(__name__))
    assert ('evening ferry' in str(assembled.api_messages)) is not remote
    assert agent.prefill_messages == original


@pytest.mark.parametrize('remote', [True, False])
def test_ambient_context_engine_cannot_replace_owned_remote_request(tmp_path, owned_task, remote):
    import logging
    from agent.conversation_loop import _apply_context_engine_selection
    agent = _agent(tmp_path, 'https://remote.example.test/v1' if remote else 'http://127.0.0.1:8000/v1')
    engine = SimpleNamespace(select_context=Mock(return_value=[
        {'role': 'user', 'content': 'Private profile history: evening ferry.'}]))
    agent.context_compressor = engine
    history = [{'role': 'user', 'content': 'Public request.'}]
    before = deepcopy(history)
    result = _apply_context_engine_selection(agent, deepcopy(history), history, history[0], logger=logging.getLogger(__name__))
    assert engine.select_context.called is not remote
    assert ('evening ferry' in str(result)) is not remote
    assert history == before


@pytest.mark.parametrize('kind', ['sync', 'async', 'stream'])
@pytest.mark.parametrize('remote', [True, False])
def test_auxiliary_plain_history_needs_request_proof_not_just_public_main_prompt(tmp_path, owned_task, remote, kind):
    from agent import auxiliary_client as aux
    sent = []
    client = SimpleNamespace(base_url='https://remote.example.test/v1' if remote else 'http://127.0.0.1:8000/v1',
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: sent.append(kw))))
    request = {'model': 'test-model', 'messages': [{'role': 'user',
        'content': '[TOOL RESULT]: Private household preference: evening ferry.'}]}
    token = aux._RUNTIME_MAIN_CONTEXT.set({'base_url': 'http://127.0.0.1:8000/v1',
        '_remote_task_context_mode': 'public', '_remote_task_public_prompt': 'Public task instructions.'})
    def invoke():
        if kind == 'async':
            async def send(body):
                sent.append(body)
            return asyncio.run(aux._relay_async_completion(client, request, provider='nvidia', create=send))
        if kind == 'sync':
            return aux._relay_sync_completion(client, request, provider='nvidia', create=lambda body: sent.append(body))
        return aux._relay_sync_stream(client, request, provider='nvidia')
    try:
        if remote:
            with pytest.raises(EgressBlocked) as blocked:
                invoke()
            assert 'unsupported_remote_task_auxiliary' in blocked.value.decision.reason_codes
            assert not sent
        else:
            invoke()
            assert 'evening ferry' in str(sent)
    finally:
        aux._RUNTIME_MAIN_CONTEXT.reset(token)
