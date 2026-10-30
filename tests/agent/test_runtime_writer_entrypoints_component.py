"""Author-only offline controls for named runtime-writer entrypoints.

Real wrappers and decorated lifecycle methods run after the existing hardfenced
fixture. Credential bodies do not run; the one composed swap stops at the real
pre-mutation route refusal, with its two route predicates replaced by literals.
Root owns containment review and execution.
"""

import builtins
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tests.agent.startup_runtime_component_support import SOURCE, EffectDenied, real_components

# Explicitly export imported fixtures used by pytest name-based injection.
__all__ = ['real_components']


@pytest.fixture
def writer_components(real_components):
    _, snapshot, _, _ = real_components
    lifecycle = importlib.import_module('agent.client_lifecycle')
    mutation = importlib.import_module('agent.runtime_mutation')
    chat = importlib.import_module('agent.chat_completion_helpers')
    helpers = sys.modules['agent.agent_runtime_helpers']
    for module in (snapshot, lifecycle, mutation, chat, helpers):
        assert Path(module.__file__).resolve().is_relative_to(SOURCE)
    assert 'run_agent' not in sys.modules
    return SimpleNamespace(snapshot=snapshot, lifecycle=lifecycle, mutation=mutation,
                           chat=chat, helpers=helpers)


def _visible_state(agent):
    return {key: value for key, value in vars(agent).items() if key != '_runtime_mutation_state'}


@pytest.mark.parametrize('operation,old_module,kwargs', [
    ('try_activate_fallback', 'chat', {'reason': None, 'reset_at': None, 'retry_exhausted': True}),
    ('try_recover_primary_transport', 'helpers',
     {'api_error': RuntimeError('synthetic transport'), 'retry_count': 2, 'max_retries': 2}),
    ('recover_with_credential_pool', 'helpers',
     {'status_code': 429, 'has_retried_429': False, 'classified_reason': None}),
])
def test_public_writer_roots_refuse_before_existing_body_while_selection_reserved(
    writer_components, monkeypatch, operation, old_module, kwargs,
):
    c = writer_components
    agent = SimpleNamespace(_primary_runtime={'model': 'preferred-model'},
                            _fallback_activated=True, model='fallback-model', client=object())
    entered = []

    def old_body(*args, **kwargs):
        entered.append(True)
        raise EffectDenied('named writer entered existing body during contention')

    monkeypatch.setattr(getattr(c, old_module), operation, old_body)
    before = _visible_state(agent)
    with c.mutation.runtime_mutation(agent, 'switch_model'):
        with pytest.raises(c.mutation.RuntimeMutationBusyError):
            getattr(c.snapshot, operation)(agent, **kwargs)
        assert _visible_state(agent) == before
    assert entered == []
    assert not c.mutation.runtime_controller(agent).busy
    assert 'run_agent' not in sys.modules


@pytest.mark.parametrize('fallback_active', [False, True])
def test_absent_primary_transport_recovery_never_retires_active_client(
    writer_components, monkeypatch, fallback_active,
):
    c = writer_components
    old_calls, retired = [], []
    agent = SimpleNamespace(
        _primary_runtime=None, _fallback_activated=fallback_active,
        provider='openrouter', model='fallback-model', base_url='https://fallback.invalid/v1',
        api_mode='chat_completions', api_key='synthetic-fallback', client=object(),
        _retire_shared_openai_client=lambda *args, **kwargs: retired.append(True),
    )

    def old_body(*args, **kwargs):
        old_calls.append(True)
        raise EffectDenied('missing preferred snapshot reached transport recovery body')

    monkeypatch.setattr(c.helpers, 'try_recover_primary_transport', old_body)
    before = _visible_state(agent)
    assert c.snapshot.try_recover_primary_transport(
        agent, RuntimeError('synthetic transport'), retry_count=2, max_retries=2,
    ) is False
    assert _visible_state(agent) == before
    assert old_calls == retired == []
    assert 'run_agent' not in sys.modules


class CredentialReadTrap:
    """A body cannot inspect route/credential attributes even if a guard regresses."""

    def __getattribute__(self, name):
        if name not in {'_runtime_mutation_state', '__dict__'}:
            raise EffectDenied('credential body attempted an attribute read')
        return object.__getattribute__(self, name)


@pytest.mark.parametrize('method,kwargs', [
    ('_try_refresh_codex_client_credentials', {'force': True}),
    ('_try_refresh_nous_client_credentials', {'force': True}),
    ('_try_refresh_env_client_credentials', {}),
    ('_try_refresh_vertex_client_credentials', {}),
    ('_try_refresh_provider_runtime_credentials', {'reason': 'unauthorized'}),
    ('_try_refresh_anthropic_client_credentials', {}),
    ('_swap_credential', {'entry': SimpleNamespace(
        id='synthetic-entry', runtime_api_key='synthetic-key',
        runtime_base_url='https://candidate.invalid/v1',
    )}),
])
def test_decorated_credential_writers_refuse_before_body_or_credential_reads(
    writer_components, monkeypatch, method, kwargs,
):
    c = writer_components
    agent = CredentialReadTrap()
    agent.sentinel = object()
    entrypoint = getattr(c.lifecycle.ClientLifecycleMixin, method)
    original_import = builtins.__import__

    def forbid_body_import(name, *args, **kwargs):
        if name == 'run_agent' or name.startswith(('agent.', 'hermes_cli.')):
            raise EffectDenied('credential writer body attempted a production import')
        return original_import(name, *args, **kwargs)

    before = _visible_state(agent)
    with c.mutation.runtime_mutation(agent, 'switch_model'):
        monkeypatch.setattr(builtins, '__import__', forbid_body_import)
        with pytest.raises(c.mutation.RuntimeMutationBusyError):
            entrypoint(agent, **kwargs)
        assert _visible_state(agent) == before
    assert not c.mutation.runtime_controller(agent).busy
    assert 'run_agent' not in sys.modules


def test_explicit_composition_enters_real_decorated_swap_and_refuses_before_mutation(
    writer_components, monkeypatch,
):
    c = writer_components
    providers = importlib.import_module('hermes_cli.providers')
    anon = importlib.import_module('hermes_cli.anon_auth')
    for module in (providers, anon):
        assert Path(module.__file__).resolve().is_relative_to(SOURCE)
    agent = SimpleNamespace(
        provider='openrouter', model='fallback-model', base_url='https://fallback.invalid/v1',
        api_mode='chat_completions', api_key='synthetic-original', client=object(),
        _credential_pool_entry_id='original-entry',
        _client_kwargs={'api_key': 'synthetic-original', 'base_url': 'https://fallback.invalid/v1'},
    )
    entry = SimpleNamespace(id='candidate-entry', runtime_api_key='synthetic-candidate',
                            runtime_base_url='https://candidate.invalid/v1/')
    route_checks = []

    def refuse_route(provider, base_url, model):
        route_checks.append((provider, base_url, model))
        return False

    monkeypatch.setattr(providers, 'is_actual_route', lambda *args: False)
    monkeypatch.setattr(anon, 'route_can_serve_model', refuse_route)
    before = _visible_state(agent)
    swap = c.lifecycle.ClientLifecycleMixin._swap_credential
    with c.mutation.runtime_mutation(agent, 'recover_with_credential_pool') as lease:
        assert c.mutation.compose_runtime_call(
            agent, 'swap_credential', swap, agent, entry, runtime_lease=lease) is False
        with pytest.raises(c.mutation.RuntimeMutationBusyError):
            c.mutation.compose_runtime_call(agent, 'swap_credential', swap, agent, entry)
        # The explicit permit is consumed; ordinary reentry still refuses.
        with pytest.raises(c.mutation.RuntimeMutationBusyError):
            swap(agent, entry)
        assert _visible_state(agent) == before
    assert route_checks == [('openrouter', 'https://candidate.invalid/v1', 'fallback-model')]
    assert agent._client_kwargs == {
        'api_key': 'synthetic-original', 'base_url': 'https://fallback.invalid/v1',
    }
    assert not c.mutation.runtime_controller(agent).busy
    assert 'run_agent' not in sys.modules
