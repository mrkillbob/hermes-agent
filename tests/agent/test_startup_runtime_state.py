"""Private offline contract: unavailable preferred runtime is not the active fallback."""
from types import SimpleNamespace
import builtins
import sys

import pytest

from tests.agent.startup_runtime_component_support import (
    real_components, routed_agent, PRIMARY_URL, EffectDenied,
)

# Explicitly export imported fixtures used by pytest name-based injection.
__all__ = ['real_components']


@pytest.mark.parametrize('primary_available', [False, True], ids=['unavailable-primary','resolved-primary'])
def test_startup_snapshot_represents_preferred_availability(real_components, monkeypatch, primary_available):
    agent = routed_agent(real_components, monkeypatch, primary_available)
    if not primary_available:
        assert agent._primary_runtime is None
    else:
        assert (agent._primary_runtime['provider'], agent._primary_runtime['model'],
                agent._primary_runtime['base_url'], agent._primary_runtime['api_key']) == (
                    'openai', 'primary-model', PRIMARY_URL, 'synthetic-primary')
    assert 'run_agent' not in sys.modules


def test_no_primary_backup_does_not_enter_resolution_gates(real_components, monkeypatch):
    _, snapshot, _, _ = real_components
    agent = SimpleNamespace(_fallback_activated=True, _primary_runtime=None,
                            provider='openrouter', model='fallback', client=object(),
                            _fallback_index=2, _rate_limited_until=123)
    before = {k: v for k, v in vars(agent).items() if k != '_runtime_mutation_state'}
    original_import = builtins.__import__
    def no_downstream(name, *args, **kwargs):
        if name in {'agent.fallback_cooldown','hermes_cli.chat_catalog','agent.route_binding'}:
            raise EffectDenied('unavailable-primary entered downstream resolution')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_downstream)
    restore = snapshot.restore_primary_runtime
    assert restore(agent) is False
    assert {k: v for k, v in vars(agent).items() if k != '_runtime_mutation_state'} == before


def test_nonfallback_rotation_and_index_reset_preserved(real_components, monkeypatch):
    _, snapshot, _, _ = real_components
    helpers = sys.modules['agent.agent_runtime_helpers']
    seen = []
    def rotate(agent, *, runtime_lease=None):
        assert runtime_lease is not None and runtime_lease.agent is agent
        seen.append(agent)
    monkeypatch.setattr(helpers, '_revert_credential_rotation', rotate)
    agent = SimpleNamespace(_fallback_activated=False, _primary_runtime=None, _fallback_index=7)
    restore = snapshot.restore_primary_runtime
    assert restore(agent) is False
    assert agent._fallback_index == 0
    assert seen == [agent]
