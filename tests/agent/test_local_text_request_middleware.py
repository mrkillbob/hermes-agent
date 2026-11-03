"""The real request builder must not discover middleware for restricted inputs."""
from unittest.mock import MagicMock, patch
import os

import pytest

from agent import safe_worker_policy
from agent.local_text_policy import local_text_scope
from agent.turn_api_request import build_request_payload
from hermes_cli import plugins


@pytest.fixture
def agent():
    from run_agent import AIAgent

    with patch('agent.process_bootstrap.OpenAI', return_value=MagicMock()):
        agent = AIAgent(model='fixture', provider='custom', api_key='inert-fixture',
            base_url='https://provider.example.test/v1', quiet_mode=True,
            skip_context_files=True, skip_memory=True, skip_background_review=True,
            enabled_toolsets=[])
    agent._empty_content_retries = 0
    try:
        yield agent
    finally:
        agent.release_clients()


def build(agent):
    return build_request_payload(agent,
        api_messages=[{'role': 'user', 'content': 'PRIVATE-INERT-REFERENCE'}],
        _moa_prepared_request=None, tools_for_api=agent.tools, system_message='',
        api_call_count=1, api_request_id='fixture-attempt',
        effective_task_id='fixture-task', turn_id='fixture-turn')


@pytest.mark.parametrize('kind', ['scope', 'worker', 'safe_worker'])
def test_restricted_real_request_builder_skips_middleware_discovery(agent, monkeypatch, kind):
    seen = []
    monkeypatch.setattr(plugins, 'has_middleware', lambda name: seen.append(name) or True)
    monkeypatch.setattr(plugins, 'invoke_middleware', lambda *args, **kwargs: seen.append(kwargs) or [])
    monkeypatch.setattr('agent.llm_egress_lineage.barrier', lambda: seen.append('barrier'))
    if kind != 'scope':
        policy = safe_worker_policy._SafeWorkerPolicy(os.getpid(), True, True, '{}', kind == 'worker')
        monkeypatch.setattr(safe_worker_policy, '_policy', policy)
    with local_text_scope(kind == 'scope'):
        built = build(agent)
    assert seen == []
    assert built.api_kwargs['messages'][-1]['content'] == 'PRIVATE-INERT-REFERENCE'
    assert built._llm_middleware_trace == []


def test_ordinary_builder_keeps_lineage_barrier_and_middleware_rewrite(agent, monkeypatch):
    seen = []
    monkeypatch.setattr(plugins, 'has_middleware', lambda name: seen.append(name) or True)
    monkeypatch.setattr('agent.llm_egress_lineage.barrier', lambda: seen.append('barrier'))

    def rewrite(*args, **kwargs):
        seen.append('rewrite')
        return [{'request': {**kwargs['request'], 'fixture_rewrite': True}}]

    monkeypatch.setattr(plugins, 'invoke_middleware', rewrite)
    built = build(agent)
    assert seen == ['llm_request', 'barrier', 'llm_request', 'rewrite']
    assert built.api_kwargs['fixture_rewrite'] is True
    assert 'fixture_rewrite' not in built._original_api_kwargs
