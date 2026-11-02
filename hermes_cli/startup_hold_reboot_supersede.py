"""Explicit, hash-bound replacement of obsolete prospective proof, retaining all old bytes."""
import hashlib
import json
import os
import stat

from hermes_constants import get_default_hermes_root


def _read_old(fd, path, expected, entries, identity):
    from hermes_cli.startup_hold_reboot import (
        _LIMIT, _binding, _cross_boot_bindings, _digest, _hash, _refuse, validate,
    )
    from hermes_cli.startup_hold_reconcile import _version
    named, opened = path.lstat(), os.fstat(fd)
    if (not _digest(expected) or not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
            or stat.S_IMODE(named.st_mode) != 0o600 or _version(named) != _version(opened)
            or named.st_size > _LIMIT):
        _refuse()
    raw = os.read(fd, _LIMIT + 1)
    if hashlib.sha256(raw).hexdigest() != expected or len(raw) != named.st_size:
        _refuse()
    try:
        old = json.loads(raw)
    except (ValueError, UnicodeError):
        _refuse()
    # Supersession is deliberately only for the prior ledger-only schemas. An
    # incomplete or writer-bearing checkpoint needs its own recovery evidence.
    if not isinstance(old, dict) or type(old.get('schema')) is not int or old['schema'] not in (1, 2):
        _refuse()
    binding, _ = _binding(old['schema'])
    if (old.get('host') != identity['host'] or named.st_uid != binding['root']['uid']
            or validate(old, binding, entries, identity)
            or old['binding']['ledger'] == binding['ledger']):
        _refuse()
    previous, current = (_cross_boot_bindings(value, old['schema']) for value in (old['binding'], binding))
    if previous['root'] != current['root'] or previous.get('volume') != current.get('volume'):
        _refuse()
    if any(_hash(entry) in old['entries'] for entry in entries):
        _refuse()
    return raw, _version(named), old['id']


def _retain_history(path, raw):
    from hermes_cli.startup_hold_reboot import _fsync_parent
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        pending = memoryview(raw)
        while pending:
            count = os.write(fd, pending)
            if count <= 0:
                raise OSError('checkpoint history write incomplete')
            pending = pending[count:]
        os.fsync(fd)
        _fsync_parent(path)
    finally:
        os.close(fd)


def supersede(record, check, *, expected_sha256, entries):
    """Caller retains all preparation gates. No failed stage or history is removed."""
    from pm.lifetime_handles import open_hold
    from hermes_cli.startup_hold_reconcile import _version
    from hermes_cli.startup_hold_reboot import (
        _FILENAME, _fsync_parent, _native_identity, _refuse, _write_record,
    )
    root = get_default_hermes_root()
    path = root / _FILENAME
    old_fd = open_hold(path)
    if old_fd is None:
        _refuse()
    stage_fd = None
    try:
        raw, version, old_id = _read_old(old_fd, path, expected_sha256, entries, _native_identity())
        history = root / f'{_FILENAME}.{old_id}.{record["id"]}.history'
        stage = root / f'{_FILENAME}.{record["id"]}.preparing'
        _retain_history(history, raw)
        stage_fd = open_hold(stage, create=True)
        if stage_fd is None:
            _refuse()
        os.fchmod(stage_fd, 0o600)
        record['phase'] = 'preparing'
        _write_record(stage_fd, record)
        _fsync_parent(stage)
        check()
        if (_version(os.fstat(old_fd)) != version or _version(path.lstat()) != version
                or _version(os.fstat(stage_fd)) != _version(stage.lstat())):
            _refuse()
        os.replace(stage, path)
        _fsync_parent(path)
        check()
        record['phase'] = 'committed'
        _write_record(stage_fd, record)
        _fsync_parent(path)
        if (_version(os.fstat(stage_fd)) != _version(path.lstat())
                or stat.S_IMODE(os.fstat(stage_fd).st_mode) != 0o600):
            _refuse()
        return history.name
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
        os.close(old_fd)
