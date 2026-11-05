"""Tests for hermes-tools MCP A2A peer/conversation resources.

Inject a fake mcp.server and monkeypatch config + protocol providers to validate payloads.
"""

from __future__ import annotations

import importlib
import types


def _install_fake_mcp(monkeypatch):
    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")

    class FakeMCPServer:
        def __init__(self, name, instructions=None):
            self.name = name
            self.instructions = instructions
            self._resources = []
            self._tools = []

        def resource(self, uri, name=None, description=None):
            def _decorator(fn):
                self._resources.append({
                    "uri": uri, "name": name, "description": description, "fn": fn,
                })
                return fn
            return _decorator

        def add_resource(self, fn, uri, name=None, description=None):
            self._resources.append({
                "uri": uri, "name": name, "description": description, "fn": fn,
            })

        def add_tool(self, *a, **k):
            self._tools.append((a, k))

        def tool(self, *a, **k):
            def _decorator(fn):
                self._tools.append(((fn,), k))
                return fn
            return _decorator

        def run(self):
            pass

    server_mod.MCPServer = FakeMCPServer
    monkeypatch.setitem(importlib.sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(importlib.sys.modules, "mcp.server", server_mod)


class TestA2AResources:
    def test_peers_and_conversations_resources(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        _install_fake_mcp(monkeypatch)
        # Monkeypatch config + protocol providers
        class _FakeCfg:
            @staticmethod
            def load_config_readonly():
                return {
                    "a2a_agents": {
                        "researcher": {"url": "http://box:9900", "capabilities": ["web_search"], "tenant": "acme"},
                        "coder": {"url": "http://box:9901/p/test", "capabilities": ["code"], "tenant": ""},
                    }
                }
        monkeypatch.setitem(importlib.sys.modules, "hermes_cli.config", _FakeCfg)

        class _FakeProto:
            @staticmethod
            def list_conversations():
                return ["ctx-1", "ctx-2"]
        fake_pkg = types.ModuleType("plugins.platforms.a2a")
        fake_pkg.protocol = _FakeProto
        monkeypatch.setitem(importlib.sys.modules, "plugins.platforms.a2a", fake_pkg)

        # Avoid tool registration noise
        monkeypatch.setattr(m, "EXPOSED_TOOLS", tuple())
        m = importlib.reload(m)
        server = m._build_server()

        resmap = {r["name"]: r for r in server._resources}
        assert "hermes-a2a-peers" in resmap
        assert "hermes-a2a-conversations" in resmap

        peers = resmap["hermes-a2a-peers"]["fn"]()
        assert peers.get("schema") == "hermes.a2a.peers.v1"
        names = {p["name"] for p in peers.get("peers")}
        assert names == {"researcher", "coder"}

        convos = resmap["hermes-a2a-conversations"]["fn"]()
        assert convos.get("schema") == "hermes.a2a.conversations.v1"
        assert convos.get("context_ids") == ["ctx-1", "ctx-2"]
