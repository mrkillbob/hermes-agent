"""Author-only offline native-token rotation through real voice teardown.

Token resolution, native client construction, auxiliary key publication and
metadata probes are synthetic defining-seam doubles. The native refresh body,
voice capture/recovery and runtime snapshot installer remain real. Root owns
the initial RED run and containment review; no execution is implied here.
"""

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tests.agent.startup_runtime_component_support import SOURCE, real_components

# Explicitly export imported fixtures used by pytest name-based injection.
__all__ = ['real_components']


OLD_TOKEN = 'sk-ant-oat-synthetic-old'
NEW_TOKEN = 'sk-ant-oat-synthetic-new'
NATIVE_URL = 'https://api.anthropic.com'


class NativeClient:
    def __init__(self, token, base_url):
        self.token, self.base_url, self.closed = token, base_url, False

    def close(self):
        self.closed = True


class Compressor(SimpleNamespace):
    def update_model(self, **values):
        self.__dict__.update(values)
        self.threshold_tokens = 4096


@pytest.fixture
def native_component(real_components, monkeypatch):
    _, snapshot, _, _ = real_components
    names = {
        'voice': 'agent.voice_turn_route', 'binding': 'agent.route_binding',
        'mutation': 'agent.runtime_mutation', 'lifecycle': 'agent.client_lifecycle',
        'credentials': 'agent.anthropic_credentials',
        'rotation': 'agent.auxiliary_key_rotation', 'aux': 'agent.auxiliary_task_config',
        'providers': 'hermes_cli.providers', 'chat': 'agent.chat_completion_helpers',
        'compression': 'agent.conversation_compression',
    }
    modules = {name: importlib.import_module(path) for name, path in names.items()}
    helpers = sys.modules['agent.agent_runtime_helpers']
    for module in (*modules.values(), helpers):
        assert Path(module.__file__).resolve().is_relative_to(SOURCE)
    pool = SimpleNamespace(provider='anthropic')
    agent = SimpleNamespace(
        model='fallback-native-model', provider='anthropic', requested_provider='anthropic',
        api_mode='anthropic_messages', base_url=NATIVE_URL, api_key=OLD_TOKEN,
        _anthropic_api_key=OLD_TOKEN, _anthropic_base_url=NATIVE_URL,
        _is_anthropic_oauth=True, _anthropic_client=NativeClient(OLD_TOKEN, NATIVE_URL), client=None,
        _client_kwargs={'api_key': OLD_TOKEN, 'base_url': NATIVE_URL},
        _fallback_activated=True, _fallback_index=2, _rate_limit_backoff_count=3,
        _provider_fallback_active=True, _provider_fallback_route=('fallback-native-model', 'anthropic'),
        _credential_pool=pool, _credential_pool_entry_id='native-entry',
        _config_context_length=8192, _use_prompt_caching=True, _use_native_cache_layout=True,
        _reasoning_echo_flag=False, _reasoning_floor_required=False,
        _reasoning_disable_rejected=False, _reasoning_effort_rejected=False,
        reasoning_config={'enabled': True, 'effort': 'high'}, request_overrides={},
        runtime_capabilities={}, _cached_system_prompt='synthetic native prompt',
        _compression_feasibility_checked=True,
        context_compressor=Compressor(
            model='fallback-native-model', provider='anthropic', api_mode='anthropic_messages',
            base_url=NATIVE_URL, api_key=OLD_TOKEN, context_length=8192, threshold_tokens=4096,
        ),
    )
    snapshot._snapshot_primary_runtime(agent)
    assert agent._primary_runtime is None
    h = SimpleNamespace(**modules, snapshot=snapshot, helpers=helpers, agent=agent,
                        pool=pool, voice_pool=pool, voice_url=NATIVE_URL, resolved_token=NEW_TOKEN,
                        resolutions=[], refresh_builds=[], reinstalls=[], rotations=[], revalidations=[])

    def resolve_token(*, model):
        assert model == 'voice-native-model'
        h.resolutions.append(model)
        return h.resolved_token

    def build_native(token, base_url):
        h.refresh_builds.append((token, base_url))
        return NativeClient(token, base_url)

    def rebuild_native(current, rt, *, reason):
        assert current is agent and reason == 'restore_primary'
        assert rt['provider'] == 'anthropic' and rt['api_mode'] == 'anthropic_messages'
        h.reinstalls.append((rt['anthropic_api_key'], rt['anthropic_base_url']))
        current._anthropic_api_key = rt['anthropic_api_key']
        current._anthropic_base_url = rt['anthropic_base_url']
        current._is_anthropic_oauth = rt['is_anthropic_oauth']
        current._anthropic_client = NativeClient(rt['anthropic_api_key'], rt['anthropic_base_url'])
        current.client = None

    def task_config(task):
        assert task == 'voice_chat'
        return {'provider': 'anthropic', 'model': 'voice-native-model', 'base_url': h.voice_url}

    def bind_voice(current, entry, provider, model):
        assert current is agent and (provider, model) == ('anthropic', 'voice-native-model')
        old = (current.model, current.provider)
        current.model = model
        current.base_url = current._anthropic_base_url = entry['base_url']
        current._anthropic_client = NativeClient(OLD_TOKEN, entry['base_url'])
        current._credential_pool = h.voice_pool
        current._credential_pool_entry_id = 'native-entry'
        current._client_kwargs = {'api_key': OLD_TOKEN, 'base_url': entry['base_url']}
        current.context_compressor.__dict__.update(
            model=model, base_url=entry['base_url'], api_key=OLD_TOKEN, threshold_tokens=0,
        )
        return old

    agent._build_direct_anthropic_client = build_native
    agent._anthropic_oauth_flag = lambda token: True
    monkeypatch.setattr(modules['credentials'], 'resolve_anthropic_token', resolve_token)
    monkeypatch.setattr(modules['rotation'], 'rotate_runtime_main_api_key',
                        lambda old, new: h.rotations.append((old, new)))
    monkeypatch.setattr(helpers, '_rebuild_primary_client', rebuild_native)
    monkeypatch.setattr(modules['aux'], '_get_auxiliary_task_config', task_config)
    monkeypatch.setattr(modules['binding'], 'bind_route_entry', bind_voice)
    monkeypatch.setattr(modules['providers'], 'is_actual_route', lambda *args: False)
    monkeypatch.setattr(modules['chat'], 'rewrite_prompt_model_identity', lambda *args: None)
    monkeypatch.setattr(modules['chat'], '_reset_stale_streak', lambda *args: None)
    monkeypatch.setattr(modules['compression'], 'revalidate_compression_feasibility',
                        lambda current: h.revalidations.append(current.model))
    assert 'run_agent' not in sys.modules
    return h


def _begin(h):
    h.agent._voice_turn_pending = True
    h.voice.begin_voice_turn_route(h.agent, [], 'synthetic native prompt')
    record = h.mutation.runtime_controller(h.agent).voice_snapshot()[0]
    assert record.state is h.agent._voice_route_state
    assert record.preferred_snapshot is None
    assert record.state['_credential_pool'] is h.pool
    assert record.state['_credential_pool_entry_id'] == 'native-entry'
    assert record.state['snapshot']['anthropic_api_key'] == OLD_TOKEN
    return record


def _refresh(h):
    return h.lifecycle.ClientLifecycleMixin._try_refresh_anthropic_client_credentials(h.agent)


def test_successful_native_refresh_does_not_reinstall_revoked_voice_backup_token(native_component):
    h = native_component
    record = _begin(h)
    captured_state = record.state
    assert _refresh(h) is True
    assert (h.agent.api_key, h.agent._anthropic_api_key, h.agent.context_compressor.api_key) == (
        NEW_TOKEN, NEW_TOKEN, NEW_TOKEN,
    )
    assert h.agent._primary_runtime is None
    assert h.voice.end_voice_turn_route(h.agent) is None
    # Consumer assertion first: baseline must fail for the revoked token actually installed.
    assert h.agent._anthropic_client.token == NEW_TOKEN
    assert h.reinstalls == [(NEW_TOKEN, NATIVE_URL)]
    assert (h.agent.api_key, h.agent._anthropic_api_key, h.agent.context_compressor.api_key) == (
        NEW_TOKEN, NEW_TOKEN, NEW_TOKEN,
    )
    assert h.agent._client_kwargs['api_key'] == NEW_TOKEN
    assert h.agent._primary_runtime is None
    assert h.agent._credential_pool is h.pool
    assert h.agent._credential_pool_entry_id == 'native-entry'
    assert h.agent._fallback_activated is True
    assert h.agent.model == 'fallback-native-model'
    assert h.agent._is_anthropic_oauth is True
    assert record.state is captured_state
    assert h.resolutions == ['voice-native-model']
    assert h.refresh_builds == [(NEW_TOKEN, NATIVE_URL)]
    assert h.rotations == [(OLD_TOKEN, NEW_TOKEN)]
    assert h.revalidations == ['fallback-native-model']
    assert h.mutation.runtime_controller(h.agent).voice_snapshot()[:2] == (None, None)
    assert 'run_agent' not in sys.modules


@pytest.mark.parametrize('mismatch', ['endpoint', 'equal-pool-different-object', 'entry'])
def test_refreshed_token_with_ambiguous_backup_scope_defers_instead_of_reinstalling_revoked_token(
    native_component, mismatch,
):
    h = native_component
    if mismatch == 'endpoint':
        h.voice_url = NATIVE_URL + '/another-route'
    elif mismatch == 'equal-pool-different-object':
        h.voice_pool = SimpleNamespace(provider='anthropic')
        assert h.voice_pool == h.pool and h.voice_pool is not h.pool
    record = _begin(h)
    if mismatch == 'entry':
        h.agent._credential_pool_entry_id = 'another-entry'
    assert _refresh(h) is True
    assert h.agent._anthropic_api_key == NEW_TOKEN
    assert h.voice.end_voice_turn_route(h.agent) == 'deferred'
    current, debt, *_ = h.mutation.runtime_controller(h.agent).voice_snapshot()
    assert current is debt is record
    assert h.reinstalls == h.revalidations == []
    assert h.agent._anthropic_client.token == NEW_TOKEN
    assert h.agent._primary_runtime is None
    with pytest.raises(h.snapshot.RuntimeRecoveryPendingError):
        h.snapshot.restore_primary_runtime(h.agent)
    assert h.reinstalls == h.revalidations == []
    assert h.resolutions == ['voice-native-model']
    assert h.rotations == [(OLD_TOKEN, NEW_TOKEN)]


@pytest.mark.parametrize('no_refresh', ['empty-resolved-token', 'non-native-provider-guard'])
def test_unobserved_native_rotation_keeps_the_existing_backup_credential(native_component, no_refresh):
    h = native_component
    record = _begin(h)
    if no_refresh == 'empty-resolved-token':
        h.resolved_token = '   '
    else:
        h.agent.provider = 'custom'
    assert _refresh(h) is False
    assert record.state['snapshot']['anthropic_api_key'] == OLD_TOKEN
    assert h.refresh_builds == h.rotations == []
    assert h.resolutions == (['voice-native-model'] if no_refresh == 'empty-resolved-token' else [])
    assert h.voice.end_voice_turn_route(h.agent) is None
    assert h.reinstalls == [(OLD_TOKEN, NATIVE_URL)]
    assert h.agent._anthropic_client.token == OLD_TOKEN
    assert h.agent._primary_runtime is None
    assert h.agent._credential_pool is h.pool
    assert h.revalidations == ['fallback-native-model']
    assert 'run_agent' not in sys.modules
