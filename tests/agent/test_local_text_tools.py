"""No tool discovery or injected dispatch within one restricted admission."""
from types import SimpleNamespace

import pytest


def test_no_tools_survive_load_or_refresh(monkeypatch):
    import model_tools
    from toolsets import agent_tool_surface
    from tools import registry as registry_module
    from agent.agent_init import _load_tools
    from agent.local_text_policy import local_text_scope
    def forbidden(*args, **kwargs): pytest.fail('discovery must not run')
    monkeypatch.setattr(model_tools, '_compute_tool_definitions', forbidden)
    monkeypatch.setattr(registry_module, '_load_discovery_cache', forbidden)
    marker = [{'function': {'name': 'injected'}}]
    with local_text_scope(True):
        agent = SimpleNamespace()
        _load_tools(agent, ['all'], None)
        assert agent.tools == [] and agent.valid_tool_names == set()
        assert model_tools.get_tool_definitions(['all']) == []
        assert registry_module.discover_builtin_tools() == []
        assert agent_tool_surface(agent, marker) == []
    assert agent_tool_surface(SimpleNamespace(platform='cli'), marker) == marker


@pytest.mark.parametrize('entry', ['model', 'registry', 'concurrent', 'sequential', 'segmented'])
def test_injected_tool_cannot_dispatch(entry):
    import model_tools
    from tools.registry import registry
    from agent import tool_executor
    from agent.local_text_policy import LocalTextRestriction, local_text_scope
    calls = {'model': lambda: model_tools.handle_function_call('injected', {}),
             'registry': lambda: registry.dispatch('injected', {}),
             'concurrent': lambda: tool_executor.execute_tool_calls_concurrent(None, None, [], 'fixture'),
             'sequential': lambda: tool_executor.execute_tool_calls_sequential(None, None, [], 'fixture'),
             'segmented': lambda: tool_executor.execute_tool_calls_segmented(None, None, [], 'fixture')}
    with local_text_scope(True), pytest.raises(LocalTextRestriction):
        calls[entry]()
