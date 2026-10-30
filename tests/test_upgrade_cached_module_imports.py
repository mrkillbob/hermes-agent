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
from unittest.mock import patch
sys.path.insert(0, {str(root)!r})

# Warm other dependencies, then simulate a pre-update gateway whose cached
# modules predate the new helper exports. Fresh siblings are loaded from disk.
import agent.tool_executor
import agent.interrupt_control as old_interrupt
import cron.scheduler_tick
import cron.unreachable_retry
import cron.constants as old_constants
import agent.message_metadata as old_metadata
import agent.session_persistence as old_persistence
import agent.tool_dispatch_helpers as old_dispatch
modern_message = {{}}
agent.tool_executor._stamp_data_use_if_required(modern_message, {{"restricted"}})
assert modern_message["data_use"] == ["restricted"]
assert modern_message["display_metadata"]["data_use"] == ["restricted"]
modern_agent = SimpleNamespace(
    _session_db=SimpleNamespace(rewrite_pruned_rows=lambda *a, **k: None), session_id="modern",
)
modern_batch = [{{"role": "tool", "content": "stub", "_db_persisted": True}}]
with patch.object(old_persistence, "sync_rewritten_tool_rows", return_value=1) as rewrite:
    assert agent.tool_executor._sync_rewritten_tool_rows_if_needed(
        modern_agent, modern_batch, ["full body"]) == 1
    rewrite.assert_called_once()
sys.modules.pop("agent.tool_executor")
sys.modules.pop("agent.interrupt_wording")
sys.modules.pop("agent.tool_deadline")
sys.modules.pop("cron.scheduler_tick")
sys.modules.pop("cron.unreachable_retry")
sys.modules.pop("cron.recurrence_kind")
del old_interrupt.interrupt_skip_wording
del old_interrupt.interrupted_during_api_call_reason
del old_constants.is_recurring
del old_metadata.stamp_data_use
del old_persistence.sync_rewritten_tool_rows
del old_dispatch._extend_tool_timeout

tools = importlib.import_module("agent.tool_executor")
retry = importlib.import_module("cron.unreachable_retry")
tick = importlib.import_module("cron.scheduler_tick")
assert tools.interrupt_skip_wording(SimpleNamespace(_tool_interrupt_reason=None)) == "Turn interrupted"
assert tools.interrupt_skip_wording(SimpleNamespace(_tool_interrupt_reason="gateway shutdown")) == (
    "Turn aborted — gateway shutdown"
)
assert tools.interrupt_skip_wording(SimpleNamespace(_tool_interrupt_reason="user interrupt")) == "User interrupt"
assert tools._stamp_data_use_if_required({{}}, set()) is None
try:
    tools._stamp_data_use_if_required({{}}, {{"restricted"}})
except ImportError:
    pass
else:
    raise AssertionError("a nonempty data-use requirement escaped the old policy module")
unchanged = [{{"role": "tool", "content": "small", "_db_persisted": True}}]
no_writer = SimpleNamespace(_session_db=None, session_id="old")
assert tools._sync_rewritten_tool_rows_if_needed(no_writer, unchanged, ["small"]) == 0
writer = SimpleNamespace(_session_db=SimpleNamespace(rewrite_pruned_rows=lambda *a, **k: None),
                         session_id="old")
assert tools._sync_rewritten_tool_rows_if_needed(writer, unchanged, ["small"]) == 0
changed = [{{"role": "tool", "content": "stub", "_db_persisted": True}}]
try:
    tools._sync_rewritten_tool_rows_if_needed(writer, changed, ["full body"])
except ImportError:
    pass
else:
    raise AssertionError("a changed durable tool row escaped without its rewrite helper")

from agent.tool_deadline import _extend_tool_timeout
from tools.registry import registry
with patch.object(registry, "get_entry", return_value=SimpleNamespace()):
    assert _extend_tool_timeout("old-tool", 30) == 30
with patch.object(registry, "get_entry", return_value=SimpleNamespace(execution_timeout=40)):
    assert _extend_tool_timeout("new-tool", 30) == 40
    assert _extend_tool_timeout("new-tool", None) is None
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
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, {str(root)!r})
import hermes_cli.process_identity as old_owner
old_own = old_owner.own_process_groups
del old_owner.own_process_groups
del old_owner.release_process_groups
del old_owner.release_process_group_once_empty

# A new tool executor may load during a held N-1 worker's turn. Merely
# forwarding deferred diagnostics must not import the native LSP client, whose
# owned-child APIs are unavailable in that worker's cached old owner module.
import agent.tool_executor as tools
from agent.lsp import deferred
assert callable(deferred.forward_to_agent)
assert "agent.lsp.manager" not in sys.modules
assert "agent.lsp.client" not in sys.modules

# The terminal backend may also load while this cache survives. Its import is
# safe, but a real local command must fail before Popen if group custody is
# unavailable.
from tools.environments import local as local_env
fake = SimpleNamespace(env={{}}, cwd="/tmp", _recover_cwd=lambda: None)
with patch.object(local_env, "_IS_WINDOWS", False), patch.object(local_env, "_find_bash", return_value="/bin/sh"), \
     patch.object(local_env, "_make_run_env", return_value={{}}), patch.object(local_env.subprocess, "Popen") as spawn:
    for own_available in (False, True):
        if own_available:
            old_owner.own_process_groups = old_own
        try:
            local_env.LocalEnvironment._run_bash(fake, "true")
        except ImportError:
            pass
        else:
            raise AssertionError("local command spawned without both process-group custody APIs")
        spawn.assert_not_called()
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=root, text=True, capture_output=True, timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
