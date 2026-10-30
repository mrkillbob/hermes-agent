"""A general callback cannot mint permission from the owner's thread alone."""

from types import SimpleNamespace

import pytest

from tests.agent.startup_runtime_component_support import real_components

# Explicitly export imported fixtures used by pytest name-based injection.
__all__ = ['real_components']


def test_ambient_ownership_does_not_authorize_callback_composition(real_components):
    from agent.runtime_mutation import (
        RuntimeMutationBusyError, compose_runtime_call, runtime_mutation, serialized_runtime_mutation,
    )
    agent, touched = SimpleNamespace(), []

    @serialized_runtime_mutation('refresh_nous_credentials')
    def refresh(current):
        touched.append(current)

    with runtime_mutation(agent, 'switch_model') as lease:
        with pytest.raises(RuntimeMutationBusyError):
            compose_runtime_call(agent, 'refresh_nous_credentials', refresh, agent)
        assert touched == []
        compose_runtime_call(agent, 'refresh_nous_credentials', refresh, agent, runtime_lease=lease)
        assert touched == [agent]
        with pytest.raises(RuntimeMutationBusyError):
            refresh(agent)
