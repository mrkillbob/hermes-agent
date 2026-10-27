"""Binding-only proof for the reserved default board's empty legacy DB stub.

The canonical default DB lives at the Kanban root. Preserve a distinct empty
metadata-directory stub without opening it with SQLite or excluding real stores.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import stat


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _require_lock(lock, expected):
    stream = getattr(lock, '_fh', None)
    if (stream is None or stream.closed or Path(lock.path) != expected
            or _identity(os.fstat(stream.fileno())) != _identity(expected.lstat())):
        raise OSError('default shadow requires the held default ownership lock')


@dataclass(frozen=True)
class EmptyDefaultShadow:
    path: Path
    descriptor: int
    identity: tuple
    lock: object
    lock_path: Path

    def proves_empty(self, candidate):
        if candidate != self.path:
            return False
        _require_lock(self.lock, self.lock_path)
        if (_identity(os.fstat(self.descriptor)) != self.identity
                or _identity(self.path.lstat()) != self.identity
                or self.path.resolve() != self.path):
            raise OSError('default shadow changed during binding verification')
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = self.path.with_name(self.path.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise OSError('default shadow sidecar prevents empty-stub proof')
        return True


@contextmanager
def guarded_default_shadow(lock):
    from hermes_cli import kanban_db as kb

    path = kb.boards_root() / kb.DEFAULT_BOARD / 'kanban.db'
    expected_lock = kb.kanban_home() / 'runtime/board-ownership/default.lock'
    _require_lock(lock, expected_lock)
    try:
        before = path.lstat()
    except FileNotFoundError:
        yield None
        return
    if not stat.S_ISREG(before.st_mode) or path.resolve() != path:
        raise OSError('default shadow must be a regular non-aliased store')
    if before.st_size:
        yield None  # Nonempty shadows retain ordinary SQL ownership evaluation.
        return
    uid = getattr(os, 'getuid', None)
    if (uid is None or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_uid != uid() or path.resolve() != path
            or kb.kanban_db_path(kb.DEFAULT_BOARD).resolve() == path):
        raise OSError('default shadow is not a canonical distinct empty stub')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        proof = EmptyDefaultShadow(path, descriptor, _identity(before), lock, expected_lock)
        proof.proves_empty(path)
        yield proof
    finally:
        os.close(descriptor)
