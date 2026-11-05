"""Retain exact writer references for a prospective native-boot proof, never settle them."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
import stat

from hermes_constants import get_default_hermes_root


def _refuse(detail='checkpoint_inventory_invalid'):
    from hermes_cli.startup_hold_diagnostics import WriterRefusal
    raise WriterRefusal('writer_inventory', detail)


def _record(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        _refuse('record_json_invalid')
    if (not isinstance(value, dict) or set(value) != {'schema', 'pid', 'create_time', 'state'}
            or type(value['schema']) is not int or value['schema'] != 1
            or type(value['pid']) is not int or value['pid'] <= 0
            or value['state'] not in {'unsettled', 'quiescent', 'exit_ready'}):
        _refuse('record_shape_invalid')
    birth = value['create_time']
    if birth is None and value['state'] == 'unsettled':
        return value
    if type(birth) not in (int, float) or not math.isfinite(birth) or birth <= 0:
        _refuse('record_birth_invalid')
    return value


def _secure(info, directory=False):
    from hermes_cli.startup_hold_reboot import _uid
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != _uid() or info.st_mode & 0o022
            or (not directory and info.st_nlink != 1)):
        _refuse('unsafe_directory' if directory else 'unsafe_reference')


class WriterSnapshot:
    def __init__(self, directory, version, files):
        self.directory, self.version, self.files = directory, version, files
        self.value = self.check()

    def covered(self):
        from hermes_cli.process_identity import _pid_alive_matches
        covered = set()
        for _, _, raw in self.files.values():
            record = _record(raw)
            if (record['state'] in {'quiescent', 'exit_ready'}
                    and _pid_alive_matches(record['pid'], record['create_time'], strict=True) is False):
                covered.add((record['pid'], record['create_time']))
        return covered

    @staticmethod
    def _changed(current, recorded):
        return current != recorded

    def check(self):
        from hermes_cli.startup_hold_reconcile import _version
        if self.version is None:
            if self.directory.exists() or self.directory.is_symlink():
                _refuse()
            return {'directory': None, 'files': []}
        info = self.directory.lstat()
        _secure(info, directory=True)
        if self._changed(_version(info), self.version):
            _refuse('directory_identity_changed')
        if sorted(p.name for p in self.directory.iterdir()) != sorted(self.files):
            _refuse('membership_changed')
        rows = []
        for name, (fd, version, raw) in sorted(self.files.items()):
            named = (self.directory / name).lstat()
            _secure(named)
            os.lseek(fd, 0, os.SEEK_SET)
            current = os.read(fd, 1025)
            if self._changed(_version(named), version):
                _refuse('named_reference_changed')
            if self._changed(_version(os.fstat(fd)), version):
                _refuse('opened_reference_changed')
            if current != raw:
                _refuse('record_bytes_changed')
            rows.append({'name': name, 'version': list(version), 'sha256': hashlib.sha256(raw).hexdigest()})
        return {'directory': list(self.version), 'files': rows}


@contextmanager
def capture_writers(snapshot_class=None):
    """Admission is held by caller. Keep every original descriptor locked through publication."""
    from hermes_cli.startup_hold_reconcile import _version
    from pm.lifetime_handles import open_hold
    if os.name == 'nt':
        raise RuntimeError('startup_hold_reboot_unavailable')
    directory = get_default_hermes_root() / '.startup-writers'
    files = {}
    try:
        try:
            info = directory.lstat()
        except FileNotFoundError:
            yield (snapshot_class or WriterSnapshot)(directory, None, files)
            return
        _secure(info, directory=True)
        for path in sorted(directory.iterdir()):
            named = path.lstat()
            _secure(named)
            if named.st_size > 1024 or len(files) >= 128:
                _refuse('inventory_limit')
            fd = open_hold(path)
            if fd is None:
                _refuse('reference_unavailable')
            files[path.name] = (fd, _version(named), b'')
            raw = os.read(fd, 1025)
            if len(raw) != named.st_size:
                _refuse('short_read')
            _record(raw)
            files[path.name] = (fd, _version(named), raw)
        snapshot = (snapshot_class or WriterSnapshot)(directory, _version(info), files)
        yield snapshot
        snapshot.check()
    finally:
        for fd, _, _ in files.values():
            os.close(fd)


def cross_boot(value):
    """Device numbers may change across boots; native volume binding remains mandatory."""
    if (not isinstance(value, dict) or set(value) != {'directory', 'files'}
            or not isinstance(value['files'], list) or len(value['files']) > 128):
        _refuse()
    def version(raw):
        if (not isinstance(raw, list) or len(raw) != 6
                or any(type(item) is not int or item < 0 for item in raw) or raw[1] == 0):
            _refuse()
        return raw[1:]
    directory = None if value['directory'] is None else version(value['directory'])
    rows, names = [], set()
    for row in value['files']:
        from hermes_cli.startup_hold_reboot import _digest
        if (not isinstance(row, dict) or set(row) != {'name', 'version', 'sha256'}
                or not isinstance(row['name'], str) or not row['name'] or '/' in row['name']
                or row['name'] in {'.', '..'} or row['name'] in names or not _digest(row['sha256'])
                or row['version'][-1:] != [1]):
            _refuse()
        names.add(row['name'])
        rows.append(dict(row, version=version(row['version'])))
    if directory is None and rows:
        _refuse()
    return {'directory': directory, 'files': rows}


def _has_writer_checkpoint():
    from hermes_cli.startup_hold_reboot import _FILENAME, _LIMIT
    path = get_default_hermes_root() / _FILENAME
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return False
    try:
        if os.name == 'nt':
            raise RuntimeError('startup_hold_reboot_unavailable')
        info = os.fstat(fd)
        _secure(info)
        if info.st_size > _LIMIT:
            _refuse()
        try:
            record = json.loads(os.read(fd, _LIMIT + 1))
        except (ValueError, UnicodeError):
            raise RuntimeError('startup_hold_reboot_unavailable') from None
        return isinstance(record, dict) and type(record.get('schema')) is int and record['schema'] == 3
    finally:
        os.close(fd)


@contextmanager
def reconciliation_writers():
    """Select acquisition only; schema3 grants nothing until reboot_exclusion validates it."""
    from hermes_cli.startup_hold import writer_exclusion
    if _has_writer_checkpoint():
        with capture_writers() as snapshot:
            yield snapshot.covered(), snapshot
    else:
        with writer_exclusion() as covered:
            yield covered, None
