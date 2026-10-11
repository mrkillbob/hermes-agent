"""MCP server discovery concurrency and cross-process locking contracts."""

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Tests for parallel tool call support (port from openai/codex#17667)
# ---------------------------------------------------------------------------

class TestMcpParallelToolCalls:
    """Tests for the supports_parallel_tool_calls config option."""



    def test_register_mcp_servers_tracks_parallel_flag(self):
        """register_mcp_servers populates _parallel_safe_servers from config."""
        from tools.mcp_tool_discovery import register_mcp_servers
        from tools.mcp_tool import _parallel_safe_servers, _lock
        from tools.mcp_tool_schema import sanitize_mcp_name_component
        fake_config = {
            "parallel_srv": {
                "command": "echo",
                "supports_parallel_tool_calls": True,
            },
            "serial_srv": {
                "command": "echo",
                "supports_parallel_tool_calls": False,
            },
            "default_srv": {
                "command": "echo",
                # no supports_parallel_tool_calls key
            },
        }
        with patch("tools.mcp_tool._MCP_AVAILABLE", True), \
             patch("tools.mcp_tool_loop._ensure_mcp_loop"), \
             patch("tools.mcp_tool_loop._run_on_mcp_loop"), \
             patch("tools.mcp_tool_registration._existing_tool_names", return_value=[]):
            register_mcp_servers(fake_config)

        with _lock:
            assert sanitize_mcp_name_component("parallel_srv") in _parallel_safe_servers
            assert sanitize_mcp_name_component("serial_srv") not in _parallel_safe_servers
            assert sanitize_mcp_name_component("default_srv") not in _parallel_safe_servers
            # Cleanup
            _parallel_safe_servers.discard(sanitize_mcp_name_component("parallel_srv"))

# ---------------------------------------------------------------------------
# Cross-process MCP discovery lock (issue #62771)
# ---------------------------------------------------------------------------


class TestMCPDiscoveryCrossProcessLock:
    """Tests for the cross-process MCP discovery guard in discover_mcp_tools()."""

    @pytest.fixture(autouse=True)
    def _fast_retries(self):
        """Override retry constants so tests are fast."""
        from tools import mcp_tool
        orig_max = mcp_tool._MCP_DISCOVERY_LOCK_MAX_RETRIES
        orig_delay = mcp_tool._MCP_DISCOVERY_LOCK_RETRY_DELAY_S
        mcp_tool._MCP_DISCOVERY_LOCK_MAX_RETRIES = 3
        mcp_tool._MCP_DISCOVERY_LOCK_RETRY_DELAY_S = 0.01
        yield
        mcp_tool._MCP_DISCOVERY_LOCK_MAX_RETRIES = orig_max
        mcp_tool._MCP_DISCOVERY_LOCK_RETRY_DELAY_S = orig_delay

    def test_lock_acquired_path(self, tmp_path):
        """Lock acquired -> discovery runs normally, lock released at end."""
        from tools.mcp_tool_loop import _LockCookie
        from tools.mcp_tool_discovery import discover_mcp_tools

        lock_file = tmp_path / ".mcp-discovery.lock"
        fh = open(lock_file, "w", encoding="utf-8")
        cookie = _LockCookie(fh)

        def mock_acquire():
            return cookie

        mock_config = {"test_srv": {"command": "echo", "enabled": True}}
        with patch.object(cookie, "release", wraps=cookie.release) as release_spy:
            with patch("tools.mcp_tool_loop._try_acquire_mcp_discovery_lock", mock_acquire), \
                 patch("tools.mcp_tool._MCP_AVAILABLE", True), \
                 patch("tools.mcp_tool_config._load_mcp_config", return_value=mock_config), \
                 patch("tools.mcp_tool_discovery.register_mcp_servers", return_value=["mcp__test_srv__ping"]):
                result = discover_mcp_tools()
            assert result == ["mcp__test_srv__ping"]
            release_spy.assert_called_once()

    def test_lock_held_retries_exhausted_fallback(self):
        """All retry attempts see lock held -> runs discovery unguarded."""
        from tools.mcp_tool_discovery import discover_mcp_tools

        mock_config = {"test_srv": {"command": "echo", "enabled": True}}
        # Every attempt returns None (lock held)
        with patch("tools.mcp_tool_loop._try_acquire_mcp_discovery_lock", return_value=None), \
             patch("tools.mcp_tool._MCP_AVAILABLE", True), \
             patch("tools.mcp_tool_config._load_mcp_config", return_value=mock_config), \
             patch("tools.mcp_tool_discovery.register_mcp_servers") as reg_spy, \
             patch("tools.mcp_tool_registration._existing_tool_names", return_value=[]):
            discover_mcp_tools()
        # Must still run local discovery
        reg_spy.assert_called_once_with(mock_config)
