"""New siblings must import while an updating gateway retains old modules in sys.modules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_new_siblings_do_not_require_symbols_missing_from_cached_old_modules():
    root = Path(__file__).resolve().parents[1]
    code = f"""
import importlib
import sys
from types import SimpleNamespace
sys.path.insert(0, {str(root)!r})

# Warm other dependencies, then simulate a pre-update gateway whose two cached
# modules predate the new helper exports. Fresh siblings are loaded from disk.
import agent.tool_executor
import agent.interrupt_control as old_interrupt
import cron.scheduler_tick
import cron.unreachable_retry
import cron.constants as old_constants
sys.modules.pop("agent.tool_executor")
sys.modules.pop("agent.interrupt_wording")
sys.modules.pop("cron.scheduler_tick")
sys.modules.pop("cron.unreachable_retry")
sys.modules.pop("cron.recurrence_kind")
del old_interrupt.interrupt_skip_wording
del old_constants.is_recurring

tools = importlib.import_module("agent.tool_executor")
retry = importlib.import_module("cron.unreachable_retry")
tick = importlib.import_module("cron.scheduler_tick")
assert tools.interrupt_skip_wording(SimpleNamespace(_tool_interrupt_reason=None)) == "Turn interrupted"
assert tools.interrupt_skip_wording(SimpleNamespace(_tool_interrupt_reason="gateway shutdown")) == (
    "Turn aborted — gateway shutdown"
)
assert tools.interrupt_skip_wording(SimpleNamespace(_tool_interrupt_reason="user interrupt")) == "User interrupt"
for predicate in (retry.is_recurring, tick.is_recurring):
    assert predicate({{"schedule": {{"kind": "cron"}}}})
    assert predicate({{"schedule": {{"kind": "interval"}}}})
    assert not predicate({{"schedule": {{"kind": "at"}}}})
    assert not predicate({{"schedule": None}})
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=root, text=True, capture_output=True, timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_deferred_lsp_import_does_not_load_new_client_against_cached_old_owner():
    root = Path(__file__).resolve().parents[1]
    code = f"""
import sys
sys.path.insert(0, {str(root)!r})
import hermes_cli.process_identity as old_owner
del old_owner.own_process_groups
del old_owner.release_process_group_once_empty

# A new tool executor may load during a held N-1 worker's turn. Merely
# forwarding deferred diagnostics must not import the native LSP client, whose
# owned-child APIs are unavailable in that worker's cached old owner module.
import agent.tool_executor as tools
from agent.lsp import deferred
assert callable(deferred.forward_to_agent)
assert "agent.lsp.manager" not in sys.modules
assert "agent.lsp.client" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=root, text=True, capture_output=True, timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
