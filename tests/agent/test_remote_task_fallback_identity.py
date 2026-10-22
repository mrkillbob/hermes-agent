"""A verified public task stays verified across renderer-owned model label changes."""
import pytest

from agent.chat_completion_helpers import _dispatch_provider_request, rewrite_prompt_model_identity
from agent.llm_egress_firewall import EgressBlocked
from tests.agent.test_remote_task_egress import _agent


@pytest.fixture
def owned_task(monkeypatch):
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_public')
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    monkeypatch.delenv('HERMES_DELEGATED_CHILD_CONTEXT', raising=False)
    import agent.llm_egress_runtime as runtime
    monkeypatch.setattr(runtime, 'egress_enforcement_enabled', lambda: True)
    monkeypatch.setattr(runtime, '_exact_provider_secret_values', lambda: ())


def public_agent(tmp_path):
    agent = _agent(tmp_path)
    agent._cached_system_prompt = 'Public audit instructions.\nModel: primary-model\nProvider: nous'
    agent._remote_task_context_mode = 'public'
    agent._remote_task_public_prompt = agent._cached_system_prompt
    return agent


def send(agent, called):
    request = {'model': agent.model, 'messages': [
        {'role': 'system', 'content': agent._cached_system_prompt},
        {'role': 'user', 'content': 'Audit public evidence.'}]}
    return _dispatch_provider_request(agent, request, lambda wire: called.append(wire))


@pytest.mark.parametrize('provider,model', [('nvidia', 'z-ai/glm-5.3-flash'), ('nous', 'meituan/longcat-2.5-preview:free')])
def test_verified_task_fallback_identity_reaches_fake_sender(tmp_path, owned_task, provider, model):
    agent = public_agent(tmp_path)
    agent.provider, agent.model = provider, model
    original = agent._cached_system_prompt
    rewrite_prompt_model_identity(agent, model, provider)
    called = []
    send(agent, called)
    assert len(called) == 1
    assert called[0]['messages'][0]['content'] == original.replace('primary-model', model).replace('Provider: nous', f'Provider: {provider}')


@pytest.mark.parametrize('kind', ['changed-cache', 'local', 'unknown', 'restored-history'])
def test_identity_rewrite_cannot_certify_unverified_context(tmp_path, owned_task, kind):
    agent = public_agent(tmp_path)
    approved = agent._remote_task_public_prompt
    if kind == 'changed-cache':
        agent._cached_system_prompt += '\nPrivate ambient preference.'
    elif kind == 'restored-history':
        agent._remote_task_history_unverified = True
    else:
        agent._remote_task_context_mode = 'local' if kind == 'local' else None
    rewrite_prompt_model_identity(agent, 'fallback-model', 'nvidia')
    called = []
    with pytest.raises(EgressBlocked):
        send(agent, called)
    assert not called
    assert agent._remote_task_public_prompt == approved


def test_identity_rewrite_does_not_bypass_final_secret_scan(tmp_path, owned_task):
    agent = public_agent(tmp_path)
    rewrite_prompt_model_identity(agent, 'fallback-model\nSECRET_TOKEN=synthetic-private-value', 'nvidia')
    called = []
    with pytest.raises(EgressBlocked) as blocked:
        send(agent, called)
    assert 'secret_detected' in blocked.value.decision.reason_codes
    assert not called
