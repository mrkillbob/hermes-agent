"""Staged startup recovery keeps failed preparation off the active runtime/store."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest_plugins = ['tests.agent.test_startup_fallback_constructor']


@pytest.fixture
def startup_agent(offline_runtime):
    home, requests = offline_runtime
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.fallback_config import get_fallback_chain
    from run_agent import AIAgent
    from hermes_state import SessionDB
    database = SessionDB(db_path=home / 'state.db')
    agent = AIAgent(session_db=database, provider='openai-api', model='gpt-4.1-mini',
        fallback_model=get_fallback_chain(load_user_config_effective()), api_mode='chat_completions',
        enabled_toolsets=[], skip_memory=True, skip_context_files=True, skip_background_review=True,
        quiet_mode=True, max_iterations=1, save_trajectories=False, checkpoints_enabled=False, cwd=str(home))
    try:
        yield agent, requests
    finally:
        agent.close()
        database.close()


@pytest.mark.parametrize('failure', ['resolve', 'prepare'])
def test_failed_recovery_preserves_live_route_compressor_and_store(startup_agent, monkeypatch, failure):
    from agent.context_compressor import ContextCompressor
    agent, requests = startup_agent
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-primary-key')
    store = Mock()
    agent.context_compressor._session_db = store
    agent.context_compressor._session_id = 'synthetic-session'
    before = dict(agent.__dict__)
    compressor_before = dict(agent.context_compressor.__dict__)

    def fail(*args, **kwargs):
        raise ValueError('synthetic preparation failure')

    if failure == 'resolve':
        monkeypatch.setattr('agent.auxiliary_client.resolve_provider_client', fail)
    else:
        original = ContextCompressor.update_model

        def fail_after_preparing(self, *args, **kwargs):
            original(self, *args, **kwargs)
            fail()
        monkeypatch.setattr(ContextCompressor, 'update_model', fail_after_preparing)
    assert agent._restore_primary_runtime() is False
    assert agent.client is before['client']
    assert agent.provider == before['provider']
    assert agent._primary_runtime is None
    assert agent._pending_primary_intent == before['_pending_primary_intent']
    assert agent.context_compressor.__dict__ == compressor_before
    assert not store.mock_calls
    assert not requests


def test_postcommit_store_failure_keeps_coherent_primary(startup_agent, monkeypatch):
    agent, requests = startup_agent
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-primary-key')
    store = Mock()
    store.set_compression_overload_streak.side_effect = OSError('synthetic disk failure')
    agent.context_compressor._session_db = store
    agent.context_compressor._session_id = 'synthetic-session'
    assert agent._restore_primary_runtime() is True
    assert agent.provider == agent.context_compressor.provider == 'openai-api'
    assert agent.model == agent.context_compressor.model == 'gpt-4.1-mini'
    assert agent._primary_runtime['provider'] == 'openai-api'
    assert agent._fallback_activated is False
    assert agent._pending_primary_intent is None
    store.set_compression_overload_streak.assert_called_once_with('synthetic-session', 0)
    assert not requests


def test_successful_explicit_selection_supersedes_startup_intent(startup_agent):
    agent, _ = startup_agent
    assert agent._pending_primary_intent is not None
    agent.switch_model('deepseek-flash', 'deepseek', api_key='synthetic-fallback-key',
                       base_url='https://fallback.invalid/v1', api_mode='chat_completions')
    assert agent._pending_primary_intent is None
    assert agent._restore_primary_runtime() is False


@pytest.mark.parametrize('provider,requested,url', [
    ('openai-api', 'openai-api', 'https://custom.invalid/v1'),
    ('custom:fixture', 'custom:fixture', ''), ('auto', 'auto', ''),
    ('moa', 'moa', ''), ('openai', 'openai', ''),
])
def test_unsupported_intent_does_not_enter_retry_path(offline_runtime, provider, requested, url):
    from agent.startup_primary_recovery import pending_primary_intent
    agent = SimpleNamespace(provider=provider, requested_provider=requested, base_url=url,
                            model='gpt-4.1-mini', api_mode='chat_completions')
    assert pending_primary_intent(agent) is None


def test_explicit_alternate_wire_is_not_reinterpreted(offline_runtime):
    from agent.startup_primary_recovery import pending_primary_intent
    agent = SimpleNamespace(provider='openai-api', requested_provider='openai-api', base_url='',
                            model='gpt-4.1-mini', api_mode='codex_responses')
    assert pending_primary_intent(agent) is None


def test_pool_resolution_does_not_persist_uninstalled_candidate(startup_agent, monkeypatch):
    from agent.credential_pool import load_pool
    from agent.credential_pool_readonly import readonly_pool_resolution
    agent, _ = startup_agent
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-primary-key')
    persist = Mock(side_effect=AssertionError('auth store must not change during preparation'))
    monkeypatch.setattr('agent.credential_pool.persist_pool_entries', persist)
    with readonly_pool_resolution():
        pool = load_pool('openai-api')
        assert pool.has_credentials()
    persist.assert_not_called()
    monkeypatch.setattr('agent.startup_primary_recovery._prepare_primary',
                        Mock(side_effect=ValueError('late preparation failure')))
    assert agent._restore_primary_runtime() is False
    persist.assert_not_called()
    assert agent.provider == 'deepseek'


def test_custom_client_hook_is_never_invoked(offline_runtime, monkeypatch):
    from agent.startup_primary_recovery import pending_primary_intent
    from providers.base import ProviderProfile

    class CustomClientProfile(ProviderProfile):
        def create_client(self, **kwargs):
            raise AssertionError('externally owned client must not be requested')

    monkeypatch.setattr('providers.get_provider_profile', lambda _: CustomClientProfile(name='openai-api'))
    agent = SimpleNamespace(provider='openai-api', requested_provider='openai-api', base_url='',
                            model='gpt-4.1-mini', api_mode='chat_completions')
    assert pending_primary_intent(agent) is None


def test_readonly_pool_scope_preserves_other_and_later_writes(offline_runtime, monkeypatch):
    from agent.credential_pool import load_pool
    from agent.credential_pool_readonly import readonly_pool_resolution
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-primary-key')
    persist = Mock(return_value=None)
    monkeypatch.setattr('agent.credential_pool.persist_pool_entries', persist)
    existing = load_pool('openai-api')
    assert persist.called, 'ordinary pool loading must retain persistence'
    persist.reset_mock()
    with readonly_pool_resolution():
        prepared = load_pool('openai-api')
        persist.assert_not_called()
        existing._persist()
        persist.assert_called_once()
    persist.reset_mock()
    prepared._persist()
    persist.assert_called_once()


def test_failed_explicit_selection_retains_intent(startup_agent, monkeypatch):
    agent, _ = startup_agent
    intent = agent._pending_primary_intent
    monkeypatch.setattr('agent.agent_runtime_helpers.switch_model', Mock(side_effect=ValueError('selection failed')))
    with pytest.raises(ValueError, match='selection failed'):
        agent.switch_model('other-model', 'deepseek')
    assert agent._pending_primary_intent is intent


def test_new_same_named_custom_entry_does_not_steal_pending_route(startup_agent, monkeypatch):
    agent, _ = startup_agent
    resolve = Mock(side_effect=AssertionError('changed owner must not be resolved'))
    monkeypatch.setattr('agent.auxiliary_client.resolve_provider_client', resolve)
    monkeypatch.setattr('agent.custom_entry_pool.named_custom_entry',
                        lambda _: ('openai-api', {'base_url': 'https://other.invalid/v1', 'key_env': 'OTHER_KEY'}))
    assert agent._restore_primary_runtime() is False
    resolve.assert_not_called()
    assert agent.provider == 'deepseek'


def test_failed_preparation_closes_candidate_and_leaves_auth_bytes(startup_agent, offline_runtime, monkeypatch):
    from agent.auxiliary_client import resolve_provider_client
    agent, _ = startup_agent
    home, _ = offline_runtime
    before = {str(path.relative_to(home)): path.read_bytes() for path in home.rglob('auth.json')}
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-primary-key')
    candidates = []

    def resolve(*args, **kwargs):
        client, model = resolve_provider_client(*args, **kwargs)
        candidates.append(client)
        return client, model

    monkeypatch.setattr('agent.auxiliary_client.resolve_provider_client', resolve)
    monkeypatch.setattr('agent.startup_primary_recovery._prepare_primary',
                        Mock(side_effect=ValueError('candidate install refused')))
    assert agent._restore_primary_runtime() is False
    assert len(candidates) == 1 and candidates[0].is_closed()
    assert {str(path.relative_to(home)): path.read_bytes() for path in home.rglob('auth.json')} == before


def test_prepared_install_rolls_back_before_persistence(startup_agent):
    from agent.startup_primary_recovery import PreparedPrimary
    agent, _ = startup_agent
    before = dict(agent.__dict__)
    compressor_before = dict(agent.context_compressor.__dict__)

    class FailOnce(dict):
        def update(self, value):
            if self.pop('_fail_update', False):
                raise ValueError('synthetic publication failure')
            super().update(value)

    agent.__dict__ = FailOnce(before, _fail_update=True)
    writes = Mock()
    prepared = PreparedPrimary({'provider': 'openai-api'}, {'provider': 'openai-api'}, writes)
    with pytest.raises(ValueError, match='publication failure'):
        prepared.install(agent)
    assert agent.provider == before['provider']
    assert agent.context_compressor.__dict__ == compressor_before
    writes.flush.assert_not_called()
    agent.__dict__ = before


def test_unavailable_metadata_keeps_existing_fallback(startup_agent, monkeypatch):
    from agent.startup_primary_recovery import pending_primary_intent
    agent, _ = startup_agent
    monkeypatch.setattr('hermes_cli.providers.get_provider', Mock(side_effect=ValueError('metadata unavailable')))
    requested = SimpleNamespace(provider='openai-api', requested_provider='openai-api', base_url='',
                                model='gpt-4.1-mini', api_mode='chat_completions')
    assert pending_primary_intent(requested) is None
    assert agent._restore_primary_runtime() is False
    assert agent.provider == 'deepseek'


@pytest.mark.parametrize('missing', ['requested_provider', 'api_mode'])
def test_partial_initialization_cannot_infer_primary_intent(monkeypatch, missing):
    from agent.startup_primary_recovery import pending_primary_intent
    fields = dict(provider='openai-api', requested_provider='openai-api', base_url='',
                  model='gpt-4.1-mini', api_mode='chat_completions')
    del fields[missing]
    qualify = Mock(side_effect=AssertionError('incomplete route must not resolve provider metadata'))
    monkeypatch.setattr('agent.startup_primary_recovery._qualified_definition', qualify)
    assert pending_primary_intent(SimpleNamespace(**fields)) is None
    qualify.assert_not_called()
