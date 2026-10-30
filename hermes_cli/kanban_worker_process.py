"""Identity and process-group ownership for Kanban worker reclamation."""

from __future__ import annotations

import itertools
import os
import shlex

import psutil


def pid_matches_task_worker(pid: int, task_id: str) -> bool:
    from hermes_cli.update_cmd_windows import _hermes_holder_subcommand

    try:
        argv = psutil.Process(pid).cmdline()
    except (psutil.Error, OSError, ValueError):
        return False
    if _hermes_holder_subcommand(shlex.join(argv)) != "chat":
        return False
    query = f"work kanban task {task_id}"
    return any(
        flag in {"-q", "--query"} and value == query
        for flag, value in itertools.pairwise(argv)
    )


def claim_is_host_local(claim_lock, *, pid=None, task_id=None) -> bool:
    from hermes_cli import kanban_db as kb

    if not claim_lock:
        return False
    if str(claim_lock).startswith(kb._host_prefix()):
        return True
    return bool(pid and task_id and pid_matches_task_worker(int(pid), str(task_id)))


def signal_worker_tree(pid: int, sig: int) -> None:
    """Reach children in the worker's own session, never the gateway's group."""
    pid = int(pid)
    if os.name != "nt" and hasattr(os, "killpg"):
        try:
            pgid = os.getpgid(pid)
            if pgid == pid and pgid != os.getpgrp():
                os.killpg(pgid, sig)  # windows-footgun: ok — POSIX-only guarded path
                return
        except ProcessLookupError:
            if pid != os.getpgrp():
                os.killpg(pid, sig)  # windows-footgun: ok — POSIX-only guarded path
                return
        except OSError:
            pass
    os.kill(pid, sig)


def restart_safe_worker_argv(task, command: list[str]) -> list[str]:
    """Wrap a systemd-hosted dispatcher's worker in the shared restart-safe scope.

    Kanban workers are long-lived agentic runs that outlive the dispatcher
    tick, so they never take cron's degraded mode under the managed gateway:
    ``require_restart_safe_scope=True`` makes the helper raise
    ``RestartSafeScopeUnavailable`` there (an infrastructure spawn failure the
    dispatcher does not charge to the card). Under any other systemd unit
    (``Type=oneshot`` dispatch timers, #113612) ``outlives_parent=True`` gets the
    worker its own scope so the unit's cgroup teardown cannot kill it.
    """
    from tools.process_registry import restart_safe_gateway_child_argv

    if task.current_run_id is None:
        # Outside managed systemd this is harmless, but a managed dispatch must
        # never mint an untraceable scope.  Check topology through the shared
        # helper first, using a placeholder suffix that cannot be launched.
        dispatch = restart_safe_gateway_child_argv(
            command,
            unit_suffix=f"kanban-{task.id}-run-missing",
            require_restart_safe_scope=True,
            outlives_parent=True,
        )
        if dispatch.mode != "in_process":
            raise RuntimeError(
                "cannot create restart-safe systemd scope for Kanban worker: "
                "the claimed task has no current run id"
            )
        return command

    return restart_safe_gateway_child_argv(
        command,
        unit_suffix=f"kanban-{task.id}-run-{task.current_run_id}",
        require_restart_safe_scope=True,
        outlives_parent=True,
    ).argv
