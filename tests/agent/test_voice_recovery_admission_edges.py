"""Offline regression edges for admitting and finalizing temporary voice routes."""

import pytest

from tests.agent.startup_runtime_component_support import real_components
from tests.agent.active_runtime_test_support import active_component, begin_voice, assert_fallback_restored

# Explicitly export imported fixtures used by pytest name-based injection.
__all__ = ['active_component', 'real_components']


def test_cleanup_requested_during_configuration_precedes_voice_dispatch(active_component, monkeypatch):
    h = active_component
    config = h.aux._get_auxiliary_task_config
    ended = []

    def cleanup_from_configuration(task):
        ended.append(h.voice.end_voice_turn_route(h.agent))
        return config(task)

    monkeypatch.setattr(h.aux, '_get_auxiliary_task_config', cleanup_from_configuration)
    h.agent._voice_turn_pending = True
    try:
        h.voice.begin_voice_turn_route(h.agent, [], 'synthetic prompt')
        assert ended == ['deferred']
        assert_fallback_restored(h)
        assert h.mutation.runtime_controller(h.agent).voice_snapshot()[:2] == (None, None)
        assert len(h.factory_attempts) == len(h.revalidations) == 1
    finally:
        h.voice.end_voice_turn_route(h.agent)


@pytest.mark.parametrize('has_debt', [False, True])
def test_second_marked_begin_preserves_first_backup_and_request(active_component, has_debt):
    h = active_component
    first = begin_voice(h)
    if has_debt:
        h.fail_factory = True
        assert h.voice.end_voice_turn_route(h.agent) == 'deferred'
        h.fail_factory = False
    h.agent._voice_turn_pending = True
    attempts = len(h.factory_attempts)
    try:
        with pytest.raises(h.mutation.RuntimeMutationBusyError):
            h.voice.begin_voice_turn_route(h.agent, [], 'synthetic prompt')
        record, debt, *_ = h.mutation.runtime_controller(h.agent).voice_snapshot()
        assert record is first
        assert debt is (first if has_debt else None)
        assert first.state['snapshot']['model'] == 'fallback-resolved-model'
        assert h.agent._voice_turn_pending is True
        assert len(h.factory_attempts) == attempts
    finally:
        h.voice.end_voice_turn_route(h.agent)
