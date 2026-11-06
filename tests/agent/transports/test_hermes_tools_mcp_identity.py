"""Tests for hermes-tools MCP identity resource exposure.

We don't spin up a real MCP runtime; instead we inject a fake mcp.server
module so _build_server() can run and register the identity resource.
"""

from __future__ import annotations

import importlib
import os
import types


def _install_fake_mcp(monkeypatch):
    """Install a minimal fake mcp.server with MCPServer that records resources."""
    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")

    class FakeMCPServer:
        def __init__(self, name, instructions=None):
            self.name = name
            self.instructions = instructions
            self._resources = []
            self._tools = []

        # Newer SDK style
        def resource(self, uri, name=None, description=None):
            def _decorator(fn):
                self._resources.append({
                    "uri": uri, "name": name, "description": description, "fn": fn,
                })
                return fn
            return _decorator

        # Older SDK fallback
        def add_resource(self, fn, uri, name=None, description=None):
            self._resources.append({
                "uri": uri, "name": name, "description": description, "fn": fn,
            })

        # Tool registration isn't exercised here; keep no-ops to satisfy calls
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


class TestIdentityResource:
    def test_identity_resource_registered_and_payload(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        _install_fake_mcp(monkeypatch)
        # Keep tool registration out of scope for this test
        monkeypatch.setattr(m, "EXPOSED_TOOLS", tuple())
        # Provide sample IDs via env and a test profile
        monkeypatch.setenv("HERMES_TASK_ID", "T-123")
        monkeypatch.setenv("HERMES_SESSION_ID", "S-456")
        monkeypatch.setenv("HERMES_TURN_ID", "U-789")
        monkeypatch.setenv("HERMES_API_REQUEST_ID", "R-000")
        # Force default profile by avoiding profile import path effects
        monkeypatch.delenv("HERMES_PROFILE", raising=False)

        m = importlib.reload(m)
        server = m._build_server()
        # Exactly one identity resource should be present
        id_res = [r for r in server._resources if r.get("name") == "hermes-identity"]
        assert id_res, "hermes-identity resource not registered"
        res = id_res[0]
        assert res["uri"] == "hermes://identity"
        # Call provider and verify payload schema and fields
        payload = res["fn"]()
        assert payload.get("schema") == "hermes.identity.v1"
        assert payload.get("profile")
        assert payload.get("task_id") == "T-123"
        assert payload.get("session_id") == "S-456"
        assert payload.get("turn_id") == "U-789"
        assert payload.get("api_request_id") == "R-000"
