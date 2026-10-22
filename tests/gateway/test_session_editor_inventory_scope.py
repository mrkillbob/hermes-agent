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


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["a", "b"])
@pytest.mark.parametrize("sid", ["first", "second"])
async def test_inventory_catalog_overlay_does_not_hide_owning_profile_health(catalogs, monkeypatch, tmp_path,
                                                                          profile, sid):
    from agent.secret_scope import reset_multiplex_context, set_multiplex_context
    from gateway.session_contract import SessionRef
    from gateway.session_local_inventory import snapshot_info
    from hermes_constants import hermes_home_key, reset_hermes_home_override, set_hermes_home_override
    from tools import mcp_tool as core, mcp_tool_discovery as discovery
    from tools.registry import session_tool_scope

    c = catalogs
    homes = {name: tmp_path / name for name in ("a", "b")}
    for home in homes.values():
        home.mkdir()
    keys = {name: hermes_home_key(home) for name, home in homes.items()}
    servers = {(keys[name], "profile-server"): SimpleNamespace(
        session=object(), _registered_tool_names=[f"{name}-{i}" for i in range(count)], _sampling=None)
        for name, count in (("a", 2), ("b", 3))}
    monkeypatch.setattr(core, "_servers", servers)
    monkeypatch.setattr(core, "_server_scope_keys", {key: key[0] for key in servers})
    for name in ("_server_tool_scopes", "_server_connect_errors", "_lazy_server_tool_names",
                 "_lazy_server_configs"):
        monkeypatch.setattr(core, name, {})
    monkeypatch.setattr(core, "_server_connecting", set())
    status = discovery.get_mcp_status
    # Only config loading is synthetic; the production status/visibility code
    # reads real cached connection ledgers, never discovering or connecting.
    monkeypatch.setattr(discovery, "get_mcp_status", lambda: status({"profile-server": {"command": "fixture"}}))
    def forbidden(*args, **kwargs):
        pytest.fail("inventory attempted MCP discovery or connection")
    for name in ("_connect_server", "discover_mcp_tools", "register_mcp_servers"):
        monkeypatch.setattr(discovery, name, forbidden)
    c.authority.profile_id = str(homes["a"])
    c.authority.runner.config = SimpleNamespace(multiplex_profiles=True)
    from gateway.session_local_mcp import session_scope
    c.scope = session_scope(c.authority, "first")
    for name in c.names:
        c.registry.register(name, c.toolset, c.schemas[name], lambda **kw: pytest.fail("observer executed a tool"),
                            scope=c.scope)
    owner = c.authority if profile == "a" else SimpleNamespace(
        profile_id=str(homes["b"]), runner=SimpleNamespace(config=SimpleNamespace(multiplex_profiles=True)),
        sessions={}, agent=lambda ref: None)
    frozen = c.policy(owner, sid)
    owner.sessions[sid] = SimpleNamespace(source=SimpleNamespace(policy=frozen))
    home_token = set_hermes_home_override(homes[profile])
    multiplex_token = set_multiplex_context(True)
    before, generation = dict(c.registry._scoped_tools), c.registry._generation
    try:
        # An ambient editor scope is also cleared for health and restored to its caller.
        with session_tool_scope(c.scope):
            result = await snapshot_info(owner, SessionRef(owner.profile_id, sid))
            assert c.current_scope() == c.scope
        assert result["tool_count"] == (4 if profile == "a" and sid == "first" else 0)
        assert result["mcp_servers"] == [{"name": "profile-server", "transport": "stdio",
                "tools": 2 if profile == "a" else 3, "connected": True, "disabled": False,
                "status": "connected"}]
        assert frozen.editor_mcp_json == c.policy(owner, sid).editor_mcp_json
        assert c.registry._scoped_tools == before and c.registry._generation == generation
        assert not hasattr(owner, "_editor_mcp_held")
    finally:
        reset_multiplex_context(multiplex_token)
        reset_hermes_home_override(home_token)
