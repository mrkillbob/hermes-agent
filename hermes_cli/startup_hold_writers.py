"""Strict legacy-writer refusal, without quarantine, signals or stale-record cleanup."""
import json
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


def refuse_legacy_writers(*, covered=()):
    from hermes_cli.dashboard_procs import _iter_process_table
    from gateway.status import looks_like_gateway_runtime_command_line
    from hermes_cli.update_cmd_windows import _hermes_holder_subcommand
    from hermes_cli.process_identity import _pid_alive_matches
    for pid, command in _iter_process_table(strict=True):
        native = looks_like_gateway_runtime_command_line(command)
        standalone = _hermes_holder_subcommand(command) in {'serve', 'dashboard'}
        if pid == os.getpid() or not (native or standalone):
            continue
        if _same_process_root(pid):
            raise RuntimeError('startup_hold_writers_busy')
    path = get_default_hermes_root() / 'spawn-ledger.json'
    try:
        entries = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        raise RuntimeError('startup_hold_writers_unknown') from None
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise RuntimeError('startup_hold_writers_unknown')
    for entry in entries:
        if entry.get('purpose') not in {'serve', 'dashboard', 'mcp-helper'}:
            continue
        home = entry.get('hermes_home')
        if home and not _same_root(home):
            continue
        pid, birth = entry.get('pid'), entry.get('create_time')
        if type(pid) is not int or pid <= 0 or type(birth) not in (int, float):
            raise RuntimeError('startup_hold_writers_unknown')
        if (pid, birth) in covered:
            continue  # exact crash-safe quiescent receipt, held exclusively by the transaction
        live = _pid_alive_matches(pid, birth, strict=True)
        if live is True:
            raise RuntimeError('startup_hold_writers_busy')
        # A departed legacy parent/helper has no crash-safe descendant receipt.
        # Do not silently retire its history or treat PID death as writer exit.
        raise RuntimeError('startup_hold_writers_unknown')
