"""Stable cross-process serialization of maintained spawn-ledger writers."""
from contextlib import contextmanager
import errno
import os
import stat

from hermes_constants import get_default_hermes_root


@contextmanager
def ledger_gate(*, wait=False):
    from pm.filesystem import lock_fd
    from hermes_dir_acl import make_user_dir
    root = get_default_hermes_root()
    make_user_dir(root, mode=0o700, parents=True, exist_ok=True)
    path = root / '.spawn-ledger.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        opened, named = os.fstat(fd), path.lstat()
        getuid = getattr(os, 'getuid', None)
        if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode)
                or opened.st_nlink != 1 or (getuid is not None and opened.st_uid != getuid())
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                or not lock_fd(fd, wait=wait, timeout=5.0 if wait else 0.0)):
            raise OSError(errno.EBUSY, 'spawn ledger custody unavailable')
        yield
    finally:
        os.close(fd)
