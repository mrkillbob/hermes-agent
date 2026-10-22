"""Observer catalogs bind frozen session overlays without borrowing MCP transports."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def catalogs(monkeypatch):
    from gateway import session_ancillary, session_local, session_local_inventory, session_local_mcp, session_policy
    from hermes_cli import banner
    from tools.registry import ToolRegistry, current_session_tool_scope

    registry = ToolRegistry()
    toolset = "mcp-editor_fixture"
    names = [f"mcp__editor_fixture__{name}" for name in ("ack", "policy", "read", "submit")]
    authority = SimpleNamespace(profile_id="fixture-profile-a", runner=SimpleNamespace(),
                                sessions={}, agent=lambda ref: None)
    scope = session_local_mcp.session_scope(authority, "first")
    schemas = {}
    for name in names:
        schema = {"name": name, "description": "Synthetic catalog tool.", "parameters": {"type": "object"}}
        registry.register(name, toolset, schema, lambda **kwargs: pytest.fail("observer executed a tool"), scope=scope)
        schemas[name] = schema

    # Keep the actual registry overlay and real observer functions, isolating only
    # model-tool discovery/probes and unrelated banner I/O from this offline test.
    model_tools = ModuleType("model_tools")
    def definitions(**kwargs):
        return [{"type": "function", "function": registry.get_schema(name)}
                for name in registry.get_all_tool_names()
                if registry.get_toolset_for_tool(name) in kwargs["enabled_toolsets"]]
    model_tools.get_tool_definitions = definitions
    model_tools.get_toolset_for_tool = registry.get_toolset_for_tool
    monkeypatch.setitem(sys.modules, "model_tools", model_tools)
    monkeypatch.setattr(session_ancillary, "authorize", lambda *a: None)
    monkeypatch.setattr(session_policy, "policy_for_source", lambda runner, source: source.policy)
    def local_info(owner, ref):
        live = owner.sessions[ref.session_id]
        agent = owner.agent(ref)
        return {"cwd": "", "lazy": agent is None,
                **session_local._classic_cli_fields(owner, ref, live, agent, live.source.policy)}
    monkeypatch.setattr(session_local, "local_session_info", local_info)
    monkeypatch.setattr(session_local_inventory, "_project", lambda cwd: None)
    monkeypatch.setattr(session_local_inventory, "_start_update_prefetch", lambda: None)
    monkeypatch.setattr(banner, "compute_toolset_availability", lambda enabled: {})
    monkeypatch.setattr(banner, "get_available_skills", dict)
    monkeypatch.setattr(banner, "get_update_result", lambda **kw: None)
    def forbidden(*args, **kwargs):
        pytest.fail("observational catalog borrowed or refroze MCP transports")
    monkeypatch.setattr(session_local_mcp, "_borrowed", forbidden)
    monkeypatch.setattr(session_local_mcp, "refreeze_editor_mcp", forbidden)

    def policy(owner, sid, *, editor=True):
        data = {"scope": session_local_mcp.session_scope(owner, sid), "schemas": schemas, "names": {"0": "editor_fixture"}}
        return SimpleNamespace(editor_mcp_json=json.dumps(data) if editor else None, source="tui", platform="cli",
                               current_toolsets=lambda config: [toolset], config=lambda *a, **kw: {}, cwd="")
    async def read(surface, owner, sid, frozen):
        from gateway.session_contract import SessionRef
        owner.sessions[sid] = SimpleNamespace(source=SimpleNamespace(policy=frozen))
        ref = SessionRef(owner.profile_id, sid)
        if surface == "inventory":
            result = await session_local_inventory.snapshot_info(owner, ref)
            assert result["tool_count"] == sum(map(len, result["tools"].values()))
            return sorted(name for values in result["tools"].values() for name in values)
        result = await session_ancillary.show_tools(SimpleNamespace(authority=owner), ref, {"session_id": sid})
        found = sorted(tool["name"] for section in result["sections"] for tool in section["tools"])
        assert result["total"] == len(found)
        return found
    return SimpleNamespace(registry=registry, authority=authority, scope=scope, names=names,
                           policy=policy, read=read, current_scope=current_session_tool_scope, toolset=toolset, schemas=schemas)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["inventory", "tools.show"])
async def test_observers_show_only_registered_matching_frozen_session(catalogs, surface):
    from tools.registry import session_tool_scope

    c = catalogs
    before = dict(c.registry._scoped_tools)
    generation = c.registry._generation
    frozen = c.policy(c.authority, "first")
    manifest = frozen.editor_mcp_json
    assert await c.read(surface, c.authority, "first", frozen) == sorted(c.names)
    assert frozen.editor_mcp_json == manifest
    assert await c.read(surface, c.authority, "second", c.policy(c.authority, "second")) == []
    other = SimpleNamespace(profile_id="fixture-profile-b", runner=SimpleNamespace(), sessions={}, agent=lambda ref: None)
    assert await c.read(surface, other, "first", c.policy(other, "first")) == []
    # A caller's unrelated ambient overlay must not contaminate an ordinary session.
    with session_tool_scope(c.scope):
        assert await c.read(surface, c.authority, "plain", c.policy(c.authority, "plain", editor=False)) == []
        assert c.current_scope() == c.scope
    assert c.current_scope() is None
    agent_tools = [{"type": "function", "function": c.schemas[name]} for name in c.names]
    agent = SimpleNamespace(tools=agent_tools, enabled_toolsets=[c.toolset], disabled_toolsets=[],
                            _cached_system_prompt="frozen prompt prefix", provider="synthetic")
    c.authority.agent = lambda ref: agent
    assert await c.read(surface, c.authority, "first", frozen) == sorted(c.names)
    assert agent.tools is agent_tools and agent._cached_system_prompt == "frozen prompt prefix"
    assert c.registry._scoped_tools == before
    assert c.registry._generation == generation
    assert not hasattr(c.authority, "_editor_mcp_held")


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["inventory", "tools.show"])
@pytest.mark.parametrize("mismatch", ["session", "profile"])
async def test_observers_refuse_a_frozen_overlay_bound_to_another_owner(catalogs, surface, mismatch):
    from hermes_state_runtime import RuntimeStoreError

    c = catalogs
    wrong = SimpleNamespace(profile_id="fixture-profile-b") if mismatch == "profile" else c.authority
    frozen = c.policy(wrong, "other" if mismatch == "session" else "first")
    with pytest.raises(RuntimeStoreError, match="acp_mcp_policy_conflict"):
        await c.read(surface, c.authority, "first", frozen)
    assert c.current_scope() is None
