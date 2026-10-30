"""Descendant shutdown for a PTY child that shares its caller's process group."""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable

_log = logging.getLogger(__name__)


def _snapshot_descendants(pid: int) -> list:
    """Keep psutil's incarnation-aware handles before the parent can exit/reparent."""
    try:
        import psutil  # type: ignore
    except ImportError:
        _log.debug("PTY descendant snapshot unavailable", exc_info=True)
        return []
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        _log.debug("PTY descendant snapshot failed for pid %s", pid, exc_info=True)
        return []


def _psutil_alive(proc) -> bool:
    try:
        import psutil  # type: ignore
    except ImportError:
        _log.debug("PTY descendant liveness unavailable", exc_info=True)
        return False
    try:
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        _log.debug("PTY descendant liveness probe failed", exc_info=True)
        return False


def _terminate_descendants(
    descendants: list, grace: float, discard_output: Callable[[float], None],
) -> None:
    """Give shared-group helpers their first SIGHUP grace, then kill survivors.

    No killpg is safe here: the child shares the caller's group. The leader loop
    signalled only the child, so these incarnation-aware snapshots still need
    their own SIGHUP. Draining the master lets helpers finish saving on macOS;
    a helper that ignores SIGHUP must not keep the slave open after close.
    """
    import psutil  # type: ignore

    for sig in (signal.SIGHUP, signal.SIGKILL):  # windows-footgun: ok — called only by the POSIX PTY bridge
        for child in descendants:
            try:
                if sig == signal.SIGHUP:  # windows-footgun: ok — called only by the POSIX PTY bridge
                    child.send_signal(sig)
                else:
                    child.kill()
            except psutil.Error:
                _log.debug("PTY descendant signal failed", exc_info=True)
        if sig == signal.SIGHUP:  # windows-footgun: ok — called only by the POSIX PTY bridge
            deadline = time.monotonic() + grace
            while any(_psutil_alive(c) for c in descendants) and time.monotonic() < deadline:
                discard_output(0.02)
