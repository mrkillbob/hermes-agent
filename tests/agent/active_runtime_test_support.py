"""Offline doubles below real voice/reinstall/switch component control flow.

Author-only candidate: execution requires root's independent containment review.
Production modules are imported only after the existing fenced fixture runs.
"""

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tests.agent.startup_runtime_component_support import (
    SOURCE, FALLBACK_URL, PRIMARY_URL, real_components, routed_agent,
)

# Explicitly export imported fixtures used by pytest name-based injection.
__all__ = ['real_components']


class FactoryFailure(RuntimeError):
    pass


class Compressor(SimpleNamespace):
    def update_model(self, **values):
        self.__dict__.update(values)
        self.threshold_tokens = 4096


@pytest.fixture
def active_component(real_components, monkeypatch):
    _, snapshot, _, _ = real_components
    modules = {
        key: importlib.import_module(name) for key, name in {
            'voice': 'agent.voice_turn_route', 'binding': 'agent.route_binding',
            'mutation': 'agent.runtime_mutation', 'aux': 'agent.auxiliary_task_config',
            'compression': 'agent.conversation_compression',
            'providers': 'hermes_cli.providers', 'config': 'hermes_cli.config',
            'chat': 'agent.chat_completion_helpers',
        }.items()
    }
    for module in modules.values():
        assert Path(module.__file__).resolve().is_relative_to(SOURCE)
    helpers = sys.modules['agent.agent_runtime_helpers']
    agent = routed_agent(real_components, monkeypatch, False)
    agent.context_compressor = Compressor(**vars(agent.context_compressor))
    agent.client = object()
    agent.reasoning_config = {'enabled': True, 'effort': 'high'}
    agent.runtime_capabilities = {'tools': True}
    agent._fallback_index = 2
    agent._rate_limit_backoff_count = 3
    agent._provider_fallback_active = True
    agent._provider_fallback_route = ('fallback-resolved-model', 'openrouter')
    agent._credential_pool = object()
    agent._credential_pool_entry_id = 'fallback-entry'
    agent._config_context_length = 8192
    agent._reasoning_disable_rejected = True
    agent._reasoning_floor_required = False
    agent._reasoning_effort_rejected = True
    agent._compression_feasibility_checked = True
    agent._cached_system_prompt = 'synthetic prompt'
    agent._fallback_chain = []
    agent._anthropic_prompt_cache_policy = lambda **kwargs: (True, False)
    agent.request_overrides = {'extra_body': {'tier': 'synthetic'}}

    h = SimpleNamespace(
        **modules, snapshot=snapshot, helpers=helpers, agent=agent,
        factory_attempts=[], revalidations=[], switch_attempts=[],
        fail_factory=False, fail_switch=False, factory_callback=None,
        switch_callback=None, rebuilt_client=object(), selected_client=object(),
        original_pool=agent._credential_pool, original_client=agent.client,
        expected_restore_url=FALLBACK_URL, expected_restore_key='synthetic-fallback',
    )

    def rebuild(current, runtime, *, reason):
        assert current is agent and reason == 'restore_primary'
        assert runtime['base_url'] == h.expected_restore_url
        assert runtime['api_key'] == h.expected_restore_key
        h.factory_attempts.append(runtime)
        if h.factory_callback is not None:
            h.factory_callback()
        if h.fail_factory:
            raise FactoryFailure('synthetic client construction failure')
        current.client = h.rebuilt_client

    def revalidate(current):
        assert current is agent
        h.revalidations.append((current.model, current.base_url))

    def task_config(task):
        assert task == 'voice_chat'
        return {'provider': 'openrouter', 'model': 'voice-model',
                'base_url': 'https://voice.invalid/v1', 'reasoning_effort': 'low'}

    def bind_voice(current, entry, provider, model):
        assert current is agent
        assert (provider, model) == ('openrouter', 'voice-model')
        old = (current.model, current.provider)
        current.model = model
        current.provider = current.requested_provider = provider
        current.base_url = entry['base_url']
        current.api_key = 'synthetic-voice'
        current._client_kwargs = {'api_key': current.api_key, 'base_url': current.base_url}
        current.client = object()
        current._credential_pool = object()
        current._credential_pool_entry_id = 'voice-entry'
        current._fallback_index = 8
        current._rate_limit_backoff_count = 9
        current._provider_fallback_route = ('voice-model', 'openrouter')
        current._config_context_length = 2048
        current._use_prompt_caching = False
        current._use_native_cache_layout = True
        current._reasoning_echo_flag = False
        current.context_compressor.__dict__.update(
            model=model, provider=provider, base_url=current.base_url,
            api_key=current.api_key, context_length=2048, threshold_tokens=0,
        )
        return old

    monkeypatch.setattr(helpers, '_rebuild_primary_client', rebuild)
    monkeypatch.setattr(modules['compression'], 'revalidate_compression_feasibility', revalidate)
    monkeypatch.setattr(modules['aux'], '_get_auxiliary_task_config', task_config)
    monkeypatch.setattr(modules['binding'], 'bind_route_entry', bind_voice)
    monkeypatch.setattr(modules['providers'], 'is_actual_route', lambda *args: False)
    monkeypatch.setattr(modules['chat'], 'rewrite_prompt_model_identity', lambda *args: None)
    monkeypatch.setattr(modules['chat'], '_reset_stale_streak', lambda *args: None)
    monkeypatch.setattr(modules['config'], 'load_config', dict)
    monkeypatch.setattr(helpers, '_apply_switched_provider_request_overrides', lambda *args: None)

    def destination(*args):
        return 'chat_completions', 'https://selected.invalid/v1', {'tools': False}

    def swap(current, new_model, new_provider, api_key, base_url, api_mode, *rest):
        assert current is agent
        h.switch_attempts.append((new_model, new_provider))
        current.model, current.provider = new_model, new_provider
        current.requested_provider = new_provider
        current.api_key, current.base_url, current.api_mode = api_key, base_url, api_mode
        current._client_kwargs = {'api_key': api_key, 'base_url': base_url}
        current.client = h.selected_client
        if h.switch_callback is not None:
            h.switch_callback()
        if h.fail_switch:
            raise FactoryFailure('synthetic destination construction failure')

    def update_compressor(current, *args):
        current.context_compressor.update_model(
            model=current.model, provider=current.provider, base_url=current.base_url,
            api_key=current.api_key, api_mode=current.api_mode, context_length=8192,
        )

    monkeypatch.setattr(helpers, '_resolve_switch_destination', destination)
    monkeypatch.setattr(helpers, '_swap_switch_runtime', swap)
    monkeypatch.setattr(helpers, '_resolve_switch_context_length', lambda *args: ([], 8192))
    monkeypatch.setattr(helpers, '_update_switch_compressor', update_compressor)
    assert 'run_agent' not in sys.modules
    return h


def begin_voice(h):
    h.agent._voice_turn_pending = True
    h.voice.begin_voice_turn_route(h.agent, [], 'synthetic prompt')
    record = h.mutation.runtime_controller(h.agent).voice_snapshot()[0]
    assert record is not None
    assert record.state is h.agent._voice_route_state
    assert record.preferred_snapshot is None
    assert h.agent.model == 'voice-model'
    return record


def select_model(h):
    return h.snapshot.switch_model(
        h.agent, 'selected-model', 'openai', api_key='synthetic-selected',
        base_url='https://selected.invalid/v1', api_mode='chat_completions',
    )


def assert_fallback_restored(h):
    a = h.agent
    assert a._primary_runtime is None
    assert (a.model, a.provider, a.requested_provider, a.base_url, a.api_key) == (
        'fallback-resolved-model', 'openrouter', 'openrouter', FALLBACK_URL, 'synthetic-fallback',
    )
    assert a._client_kwargs == {
        'api_key': 'synthetic-fallback', 'base_url': FALLBACK_URL, 'timeout': 10,
    }
    assert a.client is h.rebuilt_client
    assert a._credential_pool is h.original_pool
    assert (a._fallback_activated, a._fallback_index, a._rate_limit_backoff_count) == (True, 2, 3)
    assert a._credential_pool_entry_id == 'fallback-entry'
    assert a._provider_fallback_active is True
    assert a._provider_fallback_route == ('fallback-resolved-model', 'openrouter')
    assert a.reasoning_config == {'enabled': True, 'effort': 'high'}
    assert a.request_overrides == {'extra_body': {'tier': 'synthetic'}}
    assert (a._use_prompt_caching, a._use_native_cache_layout, a._reasoning_echo_flag) == (True, False, True)
    assert (a._reasoning_disable_rejected, a._reasoning_floor_required,
            a._reasoning_effort_rejected) == (True, False, True)
    assert a._config_context_length == 8192
    cc = a.context_compressor
    assert (cc.model, cc.provider, cc.base_url, cc.api_key, cc.context_length, cc.threshold_tokens) == (
        'fallback-resolved-model', 'openrouter', FALLBACK_URL, 'synthetic-fallback', 8192, 4096,
    )
    assert 'run_agent' not in sys.modules


def prepare_resolved_primary(h, real_components, monkeypatch):
    """Record a genuinely resolved primary while leaving the active fallback intact."""
    primary = routed_agent(real_components, monkeypatch, True)
    runtime = primary._primary_runtime
    assert (runtime['provider'], runtime['model'], runtime['base_url'], runtime['api_key']) == (
        'openai', 'primary-model', PRIMARY_URL, 'synthetic-primary',
    )
    h.agent._primary_runtime = runtime
    h.expected_restore_url = PRIMARY_URL
    h.expected_restore_key = 'synthetic-primary'
    h.block_kind = None
    h.gate_calls, h.pool_rebinds, h.diagnostics = [], [], []
    h.agent._emit_diagnostic_status = h.diagnostics.append
    cooldown = importlib.import_module('agent.fallback_cooldown')
    catalog = importlib.import_module('hermes_cli.chat_catalog')
    for module in (cooldown, catalog):
        assert Path(module.__file__).resolve().is_relative_to(SOURCE)

    def entitlement(agent, provider, model):
        assert agent is h.agent and (provider, model) == ('openai', 'primary-model')
        h.gate_calls.append('entitlement')
        return h.block_kind == 'entitlement'

    def non_chat(model):
        assert model == 'primary-model'
        h.gate_calls.append('catalog')
        return h.block_kind == 'non-chat'

    def reopened(agent, provider, model, *callbacks):
        assert agent is h.agent and (provider, model) == ('openai', 'primary-model')
        h.gate_calls.append('quota')
        return False

    def reset_gate(agent, captured, provider, base_url, *callbacks):
        assert agent is h.agent and captured is runtime
        assert (provider, base_url) == ('openai', PRIMARY_URL)
        h.gate_calls.append('reset')
        return h.block_kind == 'reset', None, False

    def rebind(agent, provider, model, *callbacks, runtime_lease=None):
        assert runtime_lease is not None and runtime_lease.agent is agent
        assert agent is h.agent and (provider, model) == ('openai', 'primary-model')
        h.pool_rebinds.append((provider, model))

    monkeypatch.setattr(cooldown, '_is_entitlement_rejected', entitlement)
    monkeypatch.setattr(catalog, 'is_known_non_chat_model', non_chat)
    monkeypatch.setattr(h.helpers, '_primary_quota_reopened_early', reopened)
    monkeypatch.setattr(h.helpers, '_primary_reset_gate_blocks', reset_gate)
    monkeypatch.setattr(h.helpers, '_rebind_primary_credential_pool', rebind)
    assert (h.agent.model, h.agent.base_url, h.agent.api_key) == (
        'fallback-resolved-model', FALLBACK_URL, 'synthetic-fallback',
    )
    return runtime
