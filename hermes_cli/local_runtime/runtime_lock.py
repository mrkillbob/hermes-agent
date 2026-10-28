"""In-process proof that the ordinary cross-process router lock is still owned."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import threading

_HELD = ContextVar('local_runtime_boot_lock', default=None)


@contextmanager
def mark_owned(fd):
    info = os.fstat(fd)
    token = _HELD.set((os.getpid(), threading.get_ident(), fd, info.st_dev, info.st_ino))
    try:
        yield
    finally:
        _HELD.reset(token)


def require_owned(path):
    held = _HELD.get()
    if held is None or held[:2] != (os.getpid(), threading.get_ident()):
        raise RuntimeError('runtime_lock_required')
    try:
        current = os.fstat(held[2])
        named = path.with_name('boot.lock').stat()
        if held[3:] != (current.st_dev, current.st_ino) or held[3:] != (named.st_dev, named.st_ino):
            raise RuntimeError('runtime_lock_required')
    except OSError as exc:
        raise RuntimeError('runtime_lock_required') from exc
