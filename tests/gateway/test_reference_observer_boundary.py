"""Actual TurnRunner/agent boundary with inert HTTP; no note grant or session ACK."""
import json
import logging
import socket
from types import SimpleNamespace

import httpx
import pytest

from agent.diagnostic_reference_policy import DiagnosticPolicy, bind_agent
from agent.local_text_policy import local_text_only
from agent.learning_vault import VaultLearningNode
from gateway.session_learning_payload import CapturedReference, reference_context
from gateway.run_turn_runner import TurnRunner

PRIVATE = 'PRIVATE-REFERENCE-OBSERVER-FIXTURE'


@pytest.fixture
def bridge(monkeypatch):
    from run_agent import AIAgent

    requests, agents, sinks = [], [], []

    def wire(_transport, request):
        payload = json.loads(request.content) if request.content else {}
        if 'messages' not in payload:
            return httpx.Response(200, json={'data': []})
        requests.append(payload)
        logging.getLogger('openai.fixture').warning('SDK payload %s', payload)
        return httpx.Response(200, json={'id': 'inert-result', 'object': 'chat.completion',
            'created': 0, 'model': 'fixture', 'choices': [{'index': 0,
                'message': {'role': 'assistant', 'content': PRIVATE}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}})

    def no_network(*args, **kwargs):
        pytest.fail('real network is forbidden')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', wire)
    monkeypatch.setattr(socket.socket, 'connect', no_network)

    def make(sealed):
        agent = AIAgent(model='fixture', provider='llamacpp', api_key='inert-fixture',
            base_url='http://127.0.0.1:8000/v1', api_mode='chat_completions',
            max_iterations=1, enabled_toolsets=[], fallback_model={}, quiet_mode=True,
            skip_context_files=True, skip_memory=True, skip_background_review=True,
            load_soul_identity=False)
        agents.append(agent)
        agent._disable_streaming = True
        agent._persist_disabled, agent._end_session_on_close, agent._session_db = True, False, None
        if sealed:
            bind_agent(agent, DiagnosticPolicy('fixed host request', '{}', agent.session_id))
        source = SimpleNamespace(user_id='fixture-user', user_name='fixture', is_bot=False)
        node = VaultLearningNode('fixture-record', 'fixture', 'shared-memory', 'codex',
            'testing', 'current', None, (), PRIVATE, 'reference-only')
        reference = CapturedReference.from_node(node, raw_sha256='a' * 64)
        ctx = SimpleNamespace(session_id=agent.session_id, session_key='fixture-key', source=source,
            message=reference_context(reference), title_user_message=None,
            persist_user_display_kind=None, persist_user_display_metadata=None, moa_config=None,
            inbound_message_id=None, mute_notification_reply=False, reply_expected=True)
        runner = SimpleNamespace(_consume_pending_native_image_paths=lambda key: [])
        return TurnRunner(runner, ctx), agent

    try:
        yield make, requests, sinks
    finally:
        for agent in agents:
            agent.release_clients()


@pytest.mark.parametrize('sealed', [False, True])
def test_actual_turnrunner_dispatch_suppresses_optional_observers_only_for_sealed_agent(bridge, monkeypatch, caplog, sealed):
    from hermes_cli import plugins
    from hermes_cli.observability import shared_metrics_harness as harness

    make, requests, sinks = bridge
    runner, agent = make(sealed)
    monkeypatch.setattr(plugins, 'invoke_hook', lambda name, **kw: sinks.append(('hook', name)) or [])
    monkeypatch.setattr(plugins, 'has_hook', lambda name: True)
    monkeypatch.setattr(plugins, 'has_middleware', lambda name: sinks.append(('middleware', name)) or False)
    monkeypatch.setattr(harness, '_enabled', lambda: sinks.append(('metrics', None)) or False)
    if sealed:
        monkeypatch.setattr(harness, '_user_agent', lambda agent: pytest.fail('private metrics metadata probe'))
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert len(requests) == 1 and PRIVATE in json.dumps(requests[0])
    if sealed:
        assert sinks == []
        assert PRIVATE not in json.dumps(result) and PRIVATE not in caplog.text
        assert result['diagnostic_reference_execution']['session_acknowledged'] is False
        assert harness._active(agent) is False  # retained seal also covers later callbacks
    else:
        assert sinks and PRIVATE in json.dumps(result) and PRIVATE in caplog.text
    assert local_text_only() is False


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('nested', [False, True])
def test_sealed_managed_relay_refuses_before_copy_restore_or_provider(bridge, monkeypatch, active, nested):
    from agent import relay_llm, relay_runtime

    make, requests, sinks = bridge
    runner, agent = make(True)
    host = SimpleNamespace(managed_execution_enabled=lambda: True)

    def forbidden(*args, **kwargs):
        pytest.fail('private payload reached restore, Relay body copy or provider')

    def loop(*args, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(relay_runtime, 'get_runtime', lambda *, create: host)
            patch.setattr(relay_runtime, 'active_turn',
                lambda session_id: SimpleNamespace(lease=SimpleNamespace(host=host)) if active else None)
            patch.setattr(relay_runtime, 'resolve_execution_context', forbidden)
            patch.setattr(relay_llm, '_relay_request_body', forbidden)
            token = relay_runtime._MANAGED_CALLBACK_DEPTH.set(1 if nested else 0)
            try:
                return relay_llm.execute({'messages': [PRIVATE]}, forbidden,
                    name='fixture', model_name='fixture', session_id=agent.session_id)
            finally:
                relay_runtime._MANAGED_CALLBACK_DEPTH.reset(token)

    monkeypatch.setattr('agent.conversation_loop.run_conversation', loop)
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert result['failed'] is True and requests == [] and sinks == []
    assert PRIVATE not in json.dumps(result)
    assert local_text_only() is False


def test_actual_turnrunner_failed_provider_response_stays_body_free_and_unacknowledged(bridge, monkeypatch, caplog):
    make, requests, _ = bridge
    runner, agent = make(True)

    def error_wire(_transport, request):
        requests.append(json.loads(request.content))
        logging.getLogger('openai.fixture').error('Provider error %s', PRIVATE)
        return httpx.Response(400, json={'error': {'message': PRIVATE, 'type': 'invalid_request_error'}})

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', error_wire)
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert len(requests) == 1 and PRIVATE in json.dumps(requests[0])
    assert result['failed'] is True and PRIVATE not in json.dumps(result) and PRIVATE not in caplog.text
    assert result['diagnostic_reference_execution']['provider_receipt'] is False
    assert result['diagnostic_reference_execution']['session_acknowledged'] is False


def test_unavailable_relay_policy_inspection_refuses_without_fallback(bridge, monkeypatch):
    from agent import relay_llm, relay_runtime

    make, requests, _ = bridge
    runner, agent = make(True)

    def unavailable(**kwargs):
        raise ValueError(PRIVATE)

    def loop(*args, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(relay_runtime, 'active_turn', lambda session_id: None)
            patch.setattr(relay_runtime, 'get_runtime', unavailable)
            return relay_llm.execute({'messages': [PRIVATE]},
                lambda request: pytest.fail('unknown policy fell back to provider'),
                name='fixture', model_name='fixture', session_id=agent.session_id)

    monkeypatch.setattr('agent.conversation_loop.run_conversation', loop)
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert result['failed'] is True and requests == [] and PRIVATE not in json.dumps(result)


def test_actual_agent_loop_does_not_retry_or_fallback_after_private_relay_refusal(bridge, monkeypatch):
    from agent import relay_llm, relay_runtime

    make, requests, _ = bridge
    runner, agent = make(True)
    real_resolve = relay_llm._ManagedAttempt.resolve
    resolutions = []
    host = SimpleNamespace(managed_execution_enabled=lambda: True)

    def resolve(cls, *args, **kwargs):
        resolutions.append(True)
        with monkeypatch.context() as patch:
            patch.setattr(relay_runtime, 'active_turn', lambda session_id: None)
            patch.setattr(relay_runtime, 'get_runtime', lambda *, create: host)
            return real_resolve(*args, **kwargs)

    monkeypatch.setattr(relay_llm._ManagedAttempt, 'resolve', classmethod(resolve))
    monkeypatch.setattr('agent.turn_api_error.interruptible_backoff_sleep',
                        lambda *args, **kwargs: pytest.fail('private refusal entered retry recovery'))
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert result['failed'] is True and requests == [] and resolutions == [True]
    assert result['diagnostic_reference_execution']['provider_receipt'] is False
