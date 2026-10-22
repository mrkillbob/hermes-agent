"""Caller-owned lifetime for optional background subprocess probes."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import threading


_active_scope = ContextVar("hermes_probe_scope", default=None)
logger = logging.getLogger(__name__)


class OwnedProbe:
    """One shared cleanup attempt for the worker and its cancelling caller."""

    def __init__(self, proc, job):
        self.proc, self.job = proc, job
        self._lock = threading.Lock()
        self._closed = False

    def close(self):
        from hermes_cli._subprocess_compat import _close_job, _close_probe_group, kill_process_tree

        with self._lock:
            if self._closed:
                return
            # Latch even on denied cleanup: a later attempt could target a
            # recycled POSIX group rather than the group originally spawned.
            self._closed = True
            _close_job(self.job)
            if self.proc.returncode is None:
                # Preserve the existing bounded tree sweep (including known
                # setsid descendants), while this owner alone closes the group.
                kill_process_tree(self.proc, signal_group=False)
            try:
                _close_probe_group(self.proc)
            except OSError:
                logger.debug("Probe group cleanup failed", exc_info=True)


class ProbeScope:
    """Retain owned handles until completion; closing also refuses later spawns."""

    def __init__(self):
        self._lock = threading.RLock()
        self._closed = False
        self._processes = {}

    def spawn(self, start):
        # Closing and spawning share the lock: shutdown cannot miss a child
        # between the cancellation check and acquisition of its owned handle.
        with self._lock:
            if self._closed:
                return None
            owned = OwnedProbe(*start())
            self._processes[owned.proc] = owned
            return owned

    def release(self, proc):
        with self._lock:
            self._processes.pop(proc, None)

    def close(self):
        with self._lock:
            self._closed = True
            for owned in self._processes.values():
                owned.close()


@contextmanager
def bind_probe_scope(scope):
    token = _active_scope.set(scope)
    try:
        yield
    finally:
        _active_scope.reset(token)


def current_probe_scope():
    return _active_scope.get()
