"""Fresh tool dispatch must remain guarded across cached pre-upgrade helpers."""

import subprocess
import sys
from pathlib import Path


_SKEW_SCRIPT = """
import sys
import agent.tool_dispatch_helpers as cached
from agent.compression_marker import _COMPRESSION_MARKER_TEMPLATE

# The daemon keeps N1 helpers while its lazy executor loads from the new tree.
del cached._context_pruned_argument_paths
sys.modules.pop("agent.tool_executor", None)
import agent.tool_executor as executor

marker = _COMPRESSION_MARKER_TEMPLATE.format(omitted=1200, total=1400)
arguments = {"batch": [{"content": "exact prefix " + marker}]}
for tool in ("write_file", "plugin_custom_write", "mcp_unknown_write"):
    assert executor._context_pruned_argument_paths(tool, arguments) == ["$.batch[0].content"]
assert executor._context_pruned_argument_paths("read_file", arguments) == []
assert executor._context_pruned_argument_paths("write_file", {"content": "...[truncated]"}) == []
"""


def test_fresh_executor_blocks_pruned_effectful_arguments_with_cached_old_helpers(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", _SKEW_SCRIPT],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
