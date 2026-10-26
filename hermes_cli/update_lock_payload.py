"""Stable checkout custody beside sealed native apps, never inside signed resources.

The sidecar belongs to the canonical app, not to a profile or manifest digest.
Its immutable identity receipt is checked under the existing kernel gate. A new
payload at the same path requires an explicit lifecycle decision, never an implicit
new lock namespace while another incarnation still owns the old one.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat


def app_root(source: Path) -> Path | None:
    """Recognize the layout independently of metadata validity, including path aliases."""
    root = source.resolve()
    if len(root.parents) < 4:
        return None
    payload, resources, contents, app = root.parents[:4]
    if (payload.name == 'agent-payload' and resources.name == 'Resources'
            and contents.name == 'Contents' and app.suffix == '.app'):
        return app
    return None


def lock_path(source: Path) -> Path | None:
    app = app_root(source)
    return None if app is None else app.parent / ('.' + app.name + '.hermes-update.lock')



def open_gate(source: Path) -> tuple[int, bool]:
    """Never recreate lost custody behind an existing receipt, even if an inode is reused."""
    gate = lock_path(source)
    if gate is None:
        _refuse()
    flags = getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    receipt = gate.with_name(gate.name + '.identity')

    def receipt_present():
        try:
            receipt.lstat()
        except FileNotFoundError:
            return False
        return True

    def existing():
        try:
            return os.open(gate, os.O_RDWR | flags), True
        except PermissionError:
            try:
                return os.open(gate, os.O_RDONLY | flags), False
            except OSError:
                _refuse()  # Failed read-only fallback cannot become permission to create.

    try:
        return existing()
    except FileNotFoundError:
        if receipt_present():
            _refuse()
    try:
        fd = os.open(gate, os.O_RDWR | os.O_CREAT | os.O_EXCL | flags, 0o644)
    except FileExistsError:
        return existing()  # Another creator won; never fall back to recreating its gate.
    try:
        if receipt_present():
            _refuse()  # An old receipt cannot qualify a newly created, possibly reused inode.
    except BaseException:
        os.close(fd)
        raise
    return fd, True


def _refuse():
    raise OSError(errno.EINVAL, 'sealed checkout identity unavailable or changed')


def _read_regular(path: Path, limit: int) -> tuple[bytes, tuple]:
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or path.is_symlink():
            _refuse()
        raw = os.read(fd, limit + 1)
        after, named = os.fstat(fd), path.stat()
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        if len(raw) > limit or identity(before) != identity(after) or identity(after) != identity(named):
            _refuse()
        return raw, (before.st_dev, before.st_ino, hashlib.sha256(raw).hexdigest())
    finally:
        os.close(fd)


def identity(source: Path) -> bytes:
    root = source.resolve(strict=True)
    app = app_root(root)
    if app is None:
        _refuse()
    if (root / '.hermes-update.lock').exists() or (root / '.hermes-update.lock').is_symlink():
        _refuse()  # An older embedded-lock consumer cannot silently enter a new domain.
    try:
        manifest_raw, manifest_id = _read_regular(root.parent / 'manifest.json', 65536)
        stamp_raw, stamp_id = _read_regular(root / 'install-stamp.json', 65536)
        manifest, stamp = json.loads(manifest_raw), json.loads(stamp_raw)
        runtime = manifest['runtime']
        ref = manifest['ref']
        if (type(manifest['schema']) is not int or manifest['schema'] != 1 or manifest['target'] not in {'darwin-arm64', 'darwin-x64'}
                or not isinstance(ref, str) or re.fullmatch('[0-9a-f]{40}', ref) is None
                or manifest['repo'] != root.name or runtime['repoDir'] != root.name
                or type(stamp['schemaVersion']) is not int or stamp['schemaVersion'] != 1 or stamp['commit'] != ref or stamp['dirty'] is not False
                or stamp['payload'] != 'bundled' or stamp['distribution'] != 'desktop-app'
                or stamp['updateMechanism'] != 'external' or stamp['runtime'] != runtime
                or not isinstance(runtime['commands']['hermes'], str)):
            _refuse()
        for value in (manifest['repo'], manifest['venv'], manifest['store'], runtime['toolsDir'],
                      runtime['storePython'], runtime['sitePackages'], *runtime['commands'].values()):
            if (not isinstance(value, str) or not value or Path(value).is_absolute()
                    or not (root.parent / value).resolve().is_relative_to(root.parent)):
                _refuse()
        found = root.stat()
        container = app.stat()
        row = [1, str(root), found.st_dev, found.st_ino, container.st_dev, container.st_ino,
               manifest_id, stamp_id]
        return json.dumps(row, separators=(',', ':')).encode() + b'\n'
    except (KeyError, TypeError, ValueError, AttributeError):
        _refuse()


def verify(source: Path, fd: int | None = None, *, create: bool = False) -> None:
    """Under an owned/inherited gate, pin identity; probes only compare existing evidence."""
    gate = lock_path(source)
    if gate is None:
        return
    expected = identity(source)
    if fd is not None:
        held, named = os.fstat(fd), gate.lstat()
        if (not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
                or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)):
            _refuse()
        expected += f'{held.st_dev}:{held.st_ino}\n'.encode()
    receipt = gate.with_name(gate.name + '.identity')
    try:
        raw, _ = _read_regular(receipt, 4096)
    except FileNotFoundError:
        if not create:
            # With no gate ever published, valid sealed metadata is a free initial install.
            # An existing gate without its durable receipt is interrupted/uncertain custody.
            if gate.exists() or gate.is_symlink():
                _refuse()
            return
        if fd is None or os.fstat(fd).st_size:
            _refuse()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        receipt_fd = os.open(receipt, flags, 0o644)
        # A partial receipt remains a refusal, never an opportunity to rebind a live gate.
        with os.fdopen(receipt_fd, 'wb') as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        raw, _ = _read_regular(receipt, 4096)
    current = identity(source)
    if fd is not None:
        held, named = os.fstat(fd), gate.lstat()
        if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino) or not stat.S_ISREG(named.st_mode):
            _refuse()
        current += f'{held.st_dev}:{held.st_ino}\n'.encode()
    if raw != expected or current != expected:
        _refuse()
