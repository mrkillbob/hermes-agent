"""Private, descriptor-bound evidence for prospective router reconciliation."""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

LIMIT = 65536


def _uid():
    getuid = getattr(os, 'getuid', None)
    if getuid is None:
        raise RuntimeError('native_file_identity_unavailable')
    return getuid()


def uuid_text(value):
    parsed = uuid.UUID(value)
    if not parsed.int:
        raise ValueError('empty identity')
    return str(parsed)


def boot_identity(value):
    if not isinstance(value, dict) or set(value) != {'host', 'boot'}:
        raise ValueError('invalid identity')
    return {key: uuid_text(value[key]) for key in ('host', 'boot')}


def encoded(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')) + '\n').encode()


def version(info):
    return info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns


def private_read(path):
    uid = _uid()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid or
                before.st_nlink != 1 or before.st_mode & 0o077 or before.st_size > LIMIT):
            raise RuntimeError('private_record_unqualified')
        raw = bytearray()
        while chunk := os.read(fd, min(4096, LIMIT + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > LIMIT:
                raise RuntimeError('record_limit')
        if version(before) != version(os.fstat(fd)) or version(before) != version(path.lstat()):
            raise RuntimeError('record_changed')
        return bytes(raw), before
    finally:
        os.close(fd)


def private_directory(path):
    before = path.lstat()
    if (not stat.S_ISDIR(before.st_mode) or before.st_uid != _uid() or before.st_mode & 0o077):
        raise RuntimeError('private_recovery_directory_unqualified')
    return version(before)


def capture(root, volume_identity):
    if root.absolute() != root.resolve(strict=True):
        raise RuntimeError('runtime_path_alias')
    directory = root.lstat()
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != _uid() or directory.st_mode & 0o022:
        raise RuntimeError('runtime_directory_unqualified')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        volume = uuid_text(volume_identity(fd))
        if (directory.st_dev, directory.st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
            raise RuntimeError('runtime_directory_changed')
        raw, info = private_read(root / 'server.json')
        if info.st_dev != directory.st_dev:
            raise RuntimeError('record_volume_changed')
        named = root.lstat()
        if (directory.st_dev, directory.st_ino) != (named.st_dev, named.st_ino):
            raise RuntimeError('runtime_directory_changed')
        return raw, dict(root=str(root), root_inode=directory.st_ino, uid=directory.st_uid,
                         volume=volume, inode=info.st_ino, mode=stat.S_IMODE(info.st_mode),
                         size=info.st_size, mtime_ns=info.st_mtime_ns,
                         sha256=hashlib.sha256(raw).hexdigest())
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def exclusive_write(path, raw):
    if len(raw) > LIMIT:
        raise RuntimeError('record_limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def read_json(path):
    raw, _ = private_read(path)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError('record_shape_unqualified')
    return raw, value


def source_identity():
    root = Path(__file__).parent
    names = ('reconciliation.py', 'reconciliation_io.py', 'runtime_lock.py',
             'bootstrap.py', 'tree_custody.py', 'recovery.py')
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
    host = root.parent.parent / 'hermes_platform' / 'host'
    for name in ('boot_identity.py', 'volume_identity.py'):
        hashes['hermes_platform.host.' + name] = hashlib.sha256((host / name).read_bytes()).hexdigest()
    return hashlib.sha256(encoded(hashes)).hexdigest()
