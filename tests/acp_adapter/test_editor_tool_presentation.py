"""Frozen editor-only MCP selections remain the actual model-facing surface."""
from dataclasses import replace
import json
import sys
from types import SimpleNamespace

import pytest

from gateway.session_policy import policy_scope
from tests.acp_adapter.test_session_mcp_policy import owner, policy


@pytest.fixture
def four_tool_server(tmp_path):
    script = tmp_path / "catalog_peer.py"
    script.write_text('''import json, sys
for line in sys.stdin:
    request = json.loads(line)
    ident = request.get('id')
    if ident is None:
        continue
    method = request.get('method')
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion'], 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'catalog-fixture', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'operation_' + str(i), 'description': 'Synthetic catalog operation',
                            'inputSchema': {'type': 'object', 'properties': {}}} for i in range(4)]}
    else:
        raise AssertionError('Only discovery is permitted')
    print(json.dumps({'jsonrpc': '2.0', 'id': ident, 'result': result}), flush=True)
''')
    return {"name": "catalog-fixture", "command": sys.executable, "args": [str(script)], "env": []}


@pytest.mark.parametrize("selection", ["editor-only", "mixed", "ordinary", "implicit"])
def test_policy_agent_and_inventory_share_frozen_presentation(four_tool_server, tmp_path, monkeypatch, selection):
    import model_tools
    from agent.agent_init import _load_tools
    from gateway.session_local_inventory import _policy_tools
    from gateway.session_local_mcp import editor_mcp_inventory_scope
    from hermes_constants import get_hermes_home
    from tools.registry import registry

    # Force progressive disclosure for ordinary MCP selections without any model lookup.
    (get_hermes_home() / "config.yaml").write_text('tools:\n  tool_search:\n    enabled: "on"\n    listing: none\n')
    monkeypatch.setattr(model_tools, "_resolve_active_context_length", lambda: 128000)
    authority = owner()
    frozen = policy(authority, "presentation-fixture", four_tool_server, tmp_path)
    names = set(json.loads(frozen.editor_mcp_json)["schemas"])
    assert len(names) == 4
    if selection == "mixed":
        frozen = replace(frozen, toolsets=(*frozen.toolsets, "todo"))
    elif selection == "ordinary":
        frozen = replace(frozen, source="cli", platform="cli")
    elif selection == "implicit":
        request = json.loads(frozen.request_json)
        request.pop("toolsets")
        frozen = replace(frozen, request_json=json.dumps(request))
    agent = SimpleNamespace(platform=frozen.platform, quiet_mode=True, provider="custom", _fallback_chain=[])

    def name_set(definitions):
        return {tool["function"]["name"] for tool in definitions}

    with policy_scope(frozen, authority=authority):
        ordinary = replace(frozen, source="cli", platform="cli")
        with editor_mcp_inventory_scope(authority, ordinary, "presentation-fixture"):
            # Same overlay and generation, different presentation: warm the ordinary cache first.
            ordinary_tools = model_tools.get_tool_definitions(enabled_toolsets=list(frozen.toolsets), quiet_mode=True)
            assert {"tool_search", "tool_describe", "tool_call"} <= name_set(ordinary_tools)
        _load_tools(agent, list(frozen.toolsets), None)
        with editor_mcp_inventory_scope(authority, frozen, "presentation-fixture"):
            _, inventory = _policy_tools(frozen, {})
        assert name_set(agent.tools) == name_set(inventory)
        if selection == "editor-only":
            assert name_set(agent.tools) == names
            # An extra or narrowed selection cannot inherit the exact frozen boundary.
            expanded = model_tools.get_tool_definitions(enabled_toolsets=[*frozen.toolsets, "todo"], quiet_mode=True)
            assert not names & name_set(expanded)
            assert model_tools.get_tool_definitions(enabled_toolsets=[], quiet_mode=True) == []
        else:
            assert {"tool_search", "tool_describe", "tool_call"} <= name_set(agent.tools)
            assert not names & name_set(agent.tools)
        with editor_mcp_inventory_scope(authority, ordinary, "presentation-fixture"):
            assert model_tools.get_tool_definitions(enabled_toolsets=list(frozen.toolsets), quiet_mode=True) == ordinary_tools
        # Clearing an ambient editor scope must restore ordinary presentation and visibility.
        with editor_mcp_inventory_scope(authority, None, "ordinary"):
            assert not names & set(registry.get_all_tool_names())
        assert names <= set(registry.get_all_tool_names())
