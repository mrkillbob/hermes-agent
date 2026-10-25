"""Characterize effective child MCP schemas without opening MCP or provider connections."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


_MCP_TOOLSET = "mcp-patch-b-fixture"
_MCP_ALIAS = "patch-b-fixture-alias"
_EXTENSION_TOOLSET = "patch-b-extension-fixture"
_MCP_READ = "patch_b_mcp_read"
_MCP_SIDE_EFFECT = "patch_b_mcp_side_effect"
_EXTENSION_TOOL = "patch_b_extension_tool"


@pytest.fixture
def registered_tools():
    from tools.registry import registry

    handler_calls = []
    registered_names = []

    def register(name, toolset, handler):
        registry.register(
            name=name,
            toolset=toolset,
            schema={
                "name": name,
                "description": f"Fixture schema for {name}",
                "parameters": {"type": "object", "properties": {}},
            },
            handler=handler,
        )
        registered_names.append(name)

    register(_MCP_READ, _MCP_TOOLSET, lambda args, **kwargs: "read")
    register(_MCP_SIDE_EFFECT, _MCP_TOOLSET, lambda args, **kwargs: handler_calls.append("mcp"))
    register(_EXTENSION_TOOL, _EXTENSION_TOOLSET, lambda args, **kwargs: handler_calls.append("extension"))
    registry.register_toolset_alias(_MCP_ALIAS, _MCP_TOOLSET)

    yield SimpleNamespace(
        registry=registry,
        handler_calls=handler_calls,
        registered_names=registered_names,
        register=register,
    )

    for name in registered_names:
        registry.deregister(name)
    with registry._lock:
        registry._toolset_aliases.pop(_MCP_ALIAS, None)
    assert handler_calls == []


@pytest.fixture
def child_runtime(monkeypatch, tmp_path, registered_tools):
    import agent.subagent_lifecycle as lifecycle
    import tools.delegate_tool as delegate

    submitted = []
    children = []
    config = {}

    def submit(run_child, record, *args, **kwargs):
        submitted.append(record)
        return Future()

    monkeypatch.setattr(lifecycle._EXECUTOR, "submit", submit)
    monkeypatch.setattr(delegate, "_load_config", lambda: config)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda *args, **kwargs: {
            "tools": {"tool_search": {"enabled": "off"}},
        },
    )
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda *args, **kwargs: {})
    client = MagicMock()
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(
        "agent.context_compressor.get_model_context_length",
        lambda *args, **kwargs: 128000,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent = SimpleNamespace(
        session_id="patch-b-parent",
        enabled_toolsets=[_MCP_TOOLSET, _EXTENSION_TOOLSET],
        disabled_toolsets=[],
        valid_tool_names=set(registered_tools.registered_names),
        model="test/model",
        provider="openrouter",
        api_mode="chat_completions",
        api_key="fixture-only",
        base_url="https://openrouter.ai/api/v1",
        request_overrides={},
        prefill_messages=None,
        _session_db=None,
        _delegate_depth=0,
        _active_children=[],
        cwd=str(workspace),
    )
    service = lifecycle.SubagentLifecycleService(lambda: parent)

    def launch(allowed_toolsets):
        from agent.subagent_lifecycle import SubagentLaunchRequest

        service.launch(SubagentLaunchRequest(goal="inspect schemas only", allowed_toolsets=allowed_toolsets))
        child = submitted[-1].agent
        children.append(child)
        return child

    def build_delegate_task_child():
        from tools.delegate_tool_routes import TaskRoute

        route = TaskRoute(
            creds={
                "provider": "openrouter",
                "model": "test/model",
                "api_key": "fixture-only",
                "base_url": "https://openrouter.ai/api/v1",
                "api_mode": "chat_completions",
                "request_overrides": {},
                "command": None,
                "args": None,
            },
            routing_cfg={},
        )
        child_list, error = delegate._build_children(
            [{"goal": "inspect schemas only"}],
            [None],
            [route],
            top_role="leaf",
            max_iterations=2,
            parent_agent=parent,
            live_deleg_id=None,
            live_writers=[],
        )
        assert error is None
        child = child_list[0][2]
        children.append(child)
        return child

    yield SimpleNamespace(
        parent=parent,
        service=service,
        config=config,
        submitted=submitted,
        children=children,
        client=client,
        launch=launch,
        build_delegate_task_child=build_delegate_task_child,
        tools=registered_tools,
    )

    for child in children:
        child.close()
        lifecycle._REGISTRY.records.pop(getattr(child, "_subagent_id", ""), None)
    client.chat.completions.create.assert_not_called()


def _names(child):
    return {definition["function"]["name"] for definition in child.tools}


def test_narrow_lifecycle_request_inherits_parent_mcp_by_default(child_runtime):
    child = child_runtime.launch((_EXTENSION_TOOLSET,))

    assert _names(child) == {_EXTENSION_TOOL, _MCP_READ, _MCP_SIDE_EFFECT}
    assert child_runtime.tools.handler_calls == []


def test_false_disables_automatic_mcp_append_but_explicit_parent_mcp_stays(child_runtime):
    child_runtime.config["inherit_mcp_toolsets"] = False

    narrowed = child_runtime.launch((_EXTENSION_TOOLSET,))
    explicit = child_runtime.launch((_EXTENSION_TOOLSET, _MCP_TOOLSET))

    assert _names(narrowed) == {_EXTENSION_TOOL}
    assert _names(explicit) == {_EXTENSION_TOOL, _MCP_READ, _MCP_SIDE_EFFECT}
    assert child_runtime.tools.handler_calls == []


def test_public_lifecycle_rejects_parent_external_toolsets(child_runtime):
    from agent.subagent_lifecycle import SubagentLifecycleError

    child_runtime.parent.enabled_toolsets = [_EXTENSION_TOOLSET]

    with pytest.raises(SubagentLifecycleError, match="broaden parent"):
        child_runtime.launch((_EXTENSION_TOOLSET, _MCP_TOOLSET))

    assert child_runtime.submitted == []
    assert child_runtime.tools.handler_calls == []


def test_parent_disabled_mcp_set_remains_a_ceiling_after_append(child_runtime):
    child_runtime.parent.disabled_toolsets = [_MCP_TOOLSET]

    child = child_runtime.launch((_EXTENSION_TOOLSET,))

    assert _names(child) == {_EXTENSION_TOOL}
    assert child_runtime.tools.handler_calls == []


def test_mcp_alias_and_registry_refresh_update_the_real_child_schema(child_runtime):
    child_runtime.parent.enabled_toolsets = [_EXTENSION_TOOLSET, _MCP_ALIAS]
    alias_child = child_runtime.launch((_EXTENSION_TOOLSET,))
    assert {_MCP_READ, _MCP_SIDE_EFFECT} <= _names(alias_child)

    child_runtime.tools.registry.deregister(_MCP_SIDE_EFFECT)
    child_runtime.tools.register(
        "patch_b_mcp_refreshed", _MCP_TOOLSET, lambda args, **kwargs: "refreshed"
    )
    refreshed_child = child_runtime.launch((_EXTENSION_TOOLSET,))

    assert _MCP_SIDE_EFFECT not in _names(refreshed_child)
    assert "patch_b_mcp_refreshed" in _names(refreshed_child)
    assert _MCP_READ in _names(refreshed_child)
    assert child_runtime.tools.handler_calls == []


def test_blocked_tool_stays_removed_from_a_mixed_bundle(child_runtime, monkeypatch):
    import toolsets

    bundle = "patch-b-mixed-fixture"
    monkeypatch.setitem(
        toolsets.TOOLSETS,
        bundle,
        {"description": "local mixed bundle", "tools": ["delegate_task", _MCP_SIDE_EFFECT]},
    )
    child_runtime.parent.enabled_toolsets = [bundle, _MCP_TOOLSET]

    child = child_runtime.launch((bundle,))

    assert _MCP_SIDE_EFFECT in _names(child)
    assert "delegate_task" not in _names(child)
    assert child_runtime.tools.handler_calls == []


def test_none_and_empty_parent_toolsets_keep_the_existing_falsey_fallbacks(child_runtime):
    import model_tools
    from tools.delegate_tool_toolsets import DEFAULT_TOOLSETS, _resolve_child_toolsets

    derived_parent = SimpleNamespace(
        enabled_toolsets=None,
        valid_tool_names=set(child_runtime.tools.registered_names),
        disabled_toolsets=[],
    )
    omitted, omitted_disabled = _resolve_child_toolsets(derived_parent, None, "leaf")
    empty, empty_disabled = _resolve_child_toolsets(derived_parent, [], "leaf")

    assert set(omitted) == set(empty) == {_MCP_TOOLSET, _EXTENSION_TOOLSET}
    assert omitted_disabled == empty_disabled
    names = {
        definition["function"]["name"]
        for definition in model_tools.get_tool_definitions(
            enabled_toolsets=empty,
            disabled_toolsets=empty_disabled,
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
    }
    assert {_MCP_READ, _MCP_SIDE_EFFECT, _EXTENSION_TOOL} <= names

    no_parent_tools, no_parent_disabled = _resolve_child_toolsets(
        SimpleNamespace(enabled_toolsets=None, valid_tool_names=set(), disabled_toolsets=[]), [], "leaf"
    )
    empty_parent_tools, empty_parent_disabled = _resolve_child_toolsets(
        SimpleNamespace(enabled_toolsets=[], disabled_toolsets=[]), None, "leaf"
    )
    assert no_parent_tools == DEFAULT_TOOLSETS
    assert empty_parent_tools == []
    assert no_parent_disabled == empty_parent_disabled


def test_public_lifecycle_narrows_while_delegate_task_inherits_parent_surface(child_runtime):
    child_runtime.config["inherit_mcp_toolsets"] = False

    lifecycle_child = child_runtime.launch((_EXTENSION_TOOLSET,))
    delegate_child = child_runtime.build_delegate_task_child()

    assert _names(lifecycle_child) == {_EXTENSION_TOOL}
    assert _names(delegate_child) == {_EXTENSION_TOOL, _MCP_READ, _MCP_SIDE_EFFECT}
    assert child_runtime.tools.handler_calls == []
