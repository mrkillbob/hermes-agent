"""Strict legacy-writer refusal, without quarantine, signals or stale-record cleanup."""
import os
from pathlib import Path

from hermes_constants import get_default_hermes_root


def _same_root(home):
    if not isinstance(home, str) or not home or not Path(home).is_absolute():
        raise RuntimeError('startup_hold_writers_unknown')
    return get_default_hermes_root(home=home).resolve() == get_default_hermes_root().resolve()


def _same_process_root(pid):
    from hermes_cli.dashboard_procs import _pid_environ
    from hermes_constants import hermes_root_for_environment
    env = _pid_environ(pid)
    root = hermes_root_for_environment(env) if env is not None else None
    if root is None:
        raise RuntimeError('startup_hold_writers_unknown')
    return root.resolve() == get_default_hermes_root().resolve()


def refuse_live_writers():
    from hermes_cli.dashboard_procs import _iter_process_table
    from gateway.status import looks_like_gateway_runtime_command_line
    from hermes_cli.update_cmd_windows import _hermes_holder_subcommand
    for pid, command in _iter_process_table(strict=True):
        native = looks_like_gateway_runtime_command_line(command)
        standalone = _hermes_holder_subcommand(command) in {'serve', 'dashboard'}
        if pid == os.getpid() or not (native or standalone):
            continue
        if _same_process_root(pid):
            raise RuntimeError('startup_hold_writers_busy')


def refuse_legacy_writers(*, covered=()):
    from hermes_cli.startup_hold_reconcile import ledger_snapshot, entry_state
    refuse_live_writers()
    _, entries = ledger_snapshot()
    for entry in entries:
        state = entry_state(entry, covered)
        if state == 'busy':
            raise RuntimeError('startup_hold_writers_busy')
        if state in {'unknown', 'closure_required'}:
            raise RuntimeError('startup_hold_writers_unknown')
