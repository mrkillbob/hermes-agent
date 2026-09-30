from types import SimpleNamespace

import pytest

from agent.agent_init import _init_fallback_chain, _routed_client_kwargs


def test_local_worker_never_activates_remote_init_or_turn_fallback(monkeypatch):
    from agent import auxiliary_client

    fallback = [{"provider": "openrouter", "model": "remote-model"}]
    calls = []

    def resolve(provider, **kwargs):
        calls.append(provider)
        return None, None

    monkeypatch.setattr(auxiliary_client, "resolve_provider_client", resolve)
    monkeypatch.setenv("HERMES_KANBAN_LOCAL_ONLY", "1")
    agent = SimpleNamespace(provider="ollama", model="local-model", quiet_mode=True)
    _init_fallback_chain(agent, fallback)
    assert agent._fallback_chain == []
    with pytest.raises(RuntimeError, match="No LLM provider|Provider 'ollama'"):
        _routed_client_kwargs(agent, fallback, 1)
    assert calls == ["ollama"]

    monkeypatch.delenv("HERMES_KANBAN_LOCAL_ONLY")
    _init_fallback_chain(agent, fallback)
    assert agent._fallback_chain == fallback

@pytest.mark.parametrize(
    "task,marker,provider,fallback,restricted",
    [
        ("t_test", False, "nous", [], True),
        ("t_test", False, "anthropic", [], True),
        ("t_test", False, "openai-codex", [], True),
        ("t_test", False, "ollama", [{"provider": "nous"}], True),
        ("t_test", False, "ollama", [], False),
        ("", False, "nous", [], False),
        ("", True, "custom", [], True),
    ],
)
def test_protected_worker_loads_supported_direct_tool_scope(
    monkeypatch, task, marker, provider, fallback, restricted,
):
    import model_tools
    from agent.agent_init import _load_tools
    from tools import tool_search

    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
    if task:
        monkeypatch.setenv("HERMES_KANBAN_TASK", task)
    if marker:
        monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    # A cached adapter may still return a stale schema: the final loaded surface
    # must agree with the persisted scope used by refresh/search/dispatch.
    definitions = [
        {"type": "function", "function": {"name": name, "parameters": {}}}
        for name in ["execute_code", "terminal", "read_file", "patch"]
    ]
    observed = []

    def schemas(**kwargs):
        observed.append(kwargs)
        return definitions

    monkeypatch.setattr(model_tools, "get_tool_definitions", schemas)
    monkeypatch.setattr("agent.oneshot_footprint.prune_oneshot_tools", lambda tools: tools)
    monkeypatch.setattr("tools.connectors.turn.side_agent_tool_drops", lambda agent: set())
    agent = SimpleNamespace(
        provider=provider, _fallback_chain=fallback, quiet_mode=True,
        disabled_toolsets=["web"], enabled_toolsets=None,
    )
    _load_tools(agent, None, ["web"])
    assert {"terminal", "read_file", "patch"} <= agent.valid_tool_names
    assert ("execute_code" not in agent.valid_tool_names) is restricted
    assert ("code_execution" in agent.disabled_toolsets) is restricted
    assert ("code_execution" in observed[0]["disabled_toolsets"]) is restricted
    # Tool-search's deferrable surface is computed from the same persisted scope.
    assert "execute_code" not in tool_search.scoped_deferrable_names(agent.tools) or not restricted
