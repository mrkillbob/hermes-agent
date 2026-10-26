"""Prospective local-kernel extinction proof, with no spawn-ledger mutation.

A checkpoint records facts NOW. It cannot authorize same-boot admission or stand
in for a historical writer receipt. A new native kernel session on the same host
extinguishes local descendants even when their ancestry was lost before capture.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import uuid

from hermes_constants import get_default_hermes_root

_FILENAME = '.startup-legacy-reboot.json'
_LIMIT = 65_536



def _refuse():
    raise RuntimeError('startup_hold_reboot_unavailable')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _native_identity():
    from hermes_platform.host.boot_identity import local_boot_identity
    try:
        return local_boot_identity()
    except (OSError, ValueError, UnicodeError, AttributeError):
        _refuse()


def _uid():
    getuid = getattr(os, 'getuid', None)
    if getuid is None:
        _refuse()
    return getuid()


def _native_volume(fd):
    from hermes_platform.host.volume_identity import local_volume_identity
    try:
        return local_volume_identity(fd)
    except (OSError, ValueError, UnicodeError, AttributeError):
        _refuse()


def _source_file_hash(path):
    from hermes_cli.startup_hold_reconcile import _read_complete, _version
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                 | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _refuse()
        raw = _read_complete(fd)
        if (_version(before) != _version(os.fstat(fd))
                or _version(before) != _version(path.lstat()) or len(raw) != before.st_size):
            _refuse()
        return hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def _walk_error(_error):
    _refuse()


def _source_binding():
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    excluded = {'tests', 'node_modules', 'venv', '__pycache__', 'site-packages', 'build', 'dist'}
    for directory, names, files in os.walk(root, onerror=_walk_error):
        names[:] = sorted(name for name in names if not name.startswith('.') and name not in excluded)
        if any((Path(directory) / name).is_symlink() for name in names):
            _refuse()
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix in {'.py', '.sh', '.ps1', '.toml'} or name in {'uv.lock', 'hermes', 'activate'}:
                hashes[str(path.relative_to(root))] = _source_file_hash(path)
    if not hashes:
        _refuse()
    return dict(path=str(root), sha256=_hash(hashes))


def _binding(schema=2):
    from hermes_cli.startup_hold_reconcile import ledger_snapshot, _version
    if type(schema) is not int or schema not in (1, 2):
        _refuse()
    root = get_default_hermes_root().resolve()
    path = root / 'spawn-ledger.json'
    before = path.lstat()
    digest, entries = ledger_snapshot()
    after = path.lstat()
    info = root.stat()
    if (_version(before) != _version(after) or digest is None or os.name == 'nt'
            or info.st_uid != _uid() or info.st_mode & 0o022
            or after.st_uid != _uid() or after.st_mode & 0o022):
        _refuse()
    binding = dict(root=dict(path=str(root), device=info.st_dev, inode=info.st_ino, uid=_uid()),
                   source=_source_binding(), ledger=dict(sha256=digest, version=list(_version(after))))
    if schema == 2:
        binding['volume'] = _volume_binding(root, path, info, after)
    return binding, entries


def _volume_binding(root, path, info, ledger):
    from hermes_cli.startup_hold_reconcile import _version
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        ledger_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            volume = _native_volume(root_fd)
            if (volume != _native_volume(ledger_fd) or info.st_dev != ledger.st_dev
                    or _version(os.fstat(ledger_fd)) != _version(ledger)
                    or _version(path.lstat()) != _version(ledger)):
                _refuse()
            # Preserve full device/inode equality WITHIN this acquisition.
            for current in (os.fstat(root_fd), root.lstat()):
                if ((current.st_dev, current.st_ino, current.st_uid, current.st_mode)
                        != (info.st_dev, info.st_ino, info.st_uid, info.st_mode)):
                    _refuse()
            return volume
        finally:
            os.close(ledger_fd)
    finally:
        os.close(root_fd)


def _selector_shape(hashes, install, spawner):
    if (not isinstance(hashes, list) or not 1 <= len(hashes) <= 32
            or any(not _digest(value) for value in hashes) or len(set(hashes)) != len(hashes)
            or not isinstance(install, str) or len(install) != 12
            or any(c not in '0123456789abcdef' for c in install)
            or not isinstance(spawner, list) or len(spawner) != 2
            or type(spawner[0]) is not int or spawner[0] <= 0
            or type(spawner[1]) not in (int, float) or not 0 < spawner[1] < 1e20
            or not math.isfinite(spawner[1])):
        _refuse()


def _selected(entries, hashes, install, spawner):
    from hermes_cli.process_identity import _pid_alive_matches
    from hermes_cli.startup_hold_reconcile import entry_state
    _selector_shape(hashes, install, spawner)
    selected = [entry for entry in entries if _hash(entry) in hashes]
    if len(selected) != len(hashes) or _pid_alive_matches(*spawner, strict=True) is not False:
        _refuse()
    for entry in selected:
        if (entry.get('purpose') != 'mcp-helper' or entry.get('install') != install
                or [entry.get('spawner_pid'), entry.get('spawner_create')] != spawner
                or entry_state(entry, ()) != 'closure_required'):
            _refuse()
    return frozenset(hashes)


def _refuse_uncertain(entries):
    from hermes_cli.startup_hold_reconcile import entry_state
    for entry in entries:
        state = entry_state(entry, ())
        if state in {'busy', 'unknown'}:
            _refuse()


def _paused():
    from agent.estop import is_engaged
    if not is_engaged():
        raise RuntimeError('startup_hold_requires_pause')


def _historical_binding_shape(binding, schema):
    keys = {'root', 'source', 'ledger'} | ({'volume'} if schema == 2 else set())
    if set(binding) != keys:
        _refuse()
    root, source, ledger = (binding[key] for key in ('root', 'source', 'ledger'))
    if (not isinstance(root, dict) or set(root) != {'path', 'device', 'inode', 'uid'}
            or not isinstance(root['path'], str) or not root['path']
            or any(type(root[key]) is not int or root[key] < 0 for key in ('device', 'inode', 'uid'))
            or root['inode'] == 0
            or not isinstance(source, dict) or set(source) != {'path', 'sha256'}
            or not isinstance(source['path'], str) or not source['path'] or not _digest(source['sha256'])
            or not isinstance(ledger, dict) or set(ledger) != {'sha256', 'version'}
            or not _digest(ledger['sha256']) or not isinstance(ledger['version'], list)
            or len(ledger['version']) != 6
            or any(type(value) is not int or value < 0 for value in ledger['version'])
            or ledger['version'][1] == 0 or ledger['version'][5] != 1):
        _refuse()
    if schema == 2:
        try:
            if (not uuid.UUID(binding['volume']).int
                    or str(uuid.UUID(binding['volume'])) != binding['volume']
                    or root['device'] != ledger['version'][0]):
                _refuse()
        except (ValueError, TypeError, AttributeError):
            _refuse()


def _cross_boot_bindings(binding, schema):
    """Only schema2 replaces historical mount device numbers; never current checks."""
    if schema == 1:
        return binding
    return dict(root={key: value for key, value in binding['root'].items() if key != 'device'},
                source=binding['source'], volume=binding['volume'],
                ledger=dict(sha256=binding['ledger']['sha256'], version=binding['ledger']['version'][1:]))


def validate(record, binding, entries, identity):
    """Pure bindings plus strict current liveness; inputs are acquired under custody."""
    if (not isinstance(record, dict) or set(record) != {'schema', 'kind', 'id', 'binding',
            'host', 'boot_before', 'entries', 'install', 'spawner', 'phase'}
            or type(record.get('schema')) is not int or record['schema'] not in (1, 2)
            or record['kind'] != 'prospective_local_reboot' or record['phase'] != 'committed'
            or not isinstance(record['binding'], dict)
            or set(record['binding']) != set(binding)):
        _refuse()
    _historical_binding_shape(record['binding'], record['schema'])
    _historical_binding_shape(binding, record['schema'])
    _selector_shape(record['entries'], record['install'], record['spawner'])
    try:
        if (uuid.UUID(record['host']).int == 0 or str(uuid.UUID(record['host'])) != record['host']
                or uuid.UUID(record['id']).int == 0 or uuid.UUID(record['id']).hex != record['id'] or (uuid.UUID(record['boot_before']).int == 0
                or str(uuid.UUID(record['boot_before'])) != record['boot_before'])):
            _refuse()
    except (ValueError, TypeError, AttributeError):
        _refuse()
    previous = _cross_boot_bindings(record['binding'], record['schema'])
    current = _cross_boot_bindings(binding, record['schema'])
    if previous['ledger'] != current['ledger']:
        hashes = record['entries']
        if any(_hash(entry) in hashes for entry in entries):
            _refuse()
        # All selected rows are gone: this checkpoint supplies NO authority.
        # A later controller/host must still pass every ordinary current guard.
        return frozenset()
    if (previous != current
            or record['host'] != identity['host']):
        _refuse()
    if record['boot_before'] == identity['boot']:
        raise RuntimeError('startup_hold_reboot_required')
    return _selected(entries, record['entries'], record['install'], record['spawner'])


class _Proof:
    def __init__(self, record=None, fd=None, version=None):
        self.record, self.fd, self.version = record, fd, version
        self.rows = frozenset()

    def check(self):
        if self.record is None:
            return
        from hermes_cli.startup_hold_reconcile import _version
        from hermes_cli.startup_hold_writers import refuse_live_writers
        _paused()
        refuse_live_writers()
        binding, entries = _binding(schema=self.record.get('schema'))
        if (_version(os.fstat(self.fd)) != self.version
                or _version((get_default_hermes_root() / _FILENAME).lstat()) != self.version):
            _refuse()
        self.rows = validate(self.record, binding, entries, _native_identity())


@contextmanager
def reboot_exclusion():
    """Caller retains checkout, admission, writer, profile and ledger gates through publication."""
    from hermes_cli.startup_hold_reconcile import _version
    from pm.lifetime_handles import open_hold
    path = get_default_hermes_root() / _FILENAME
    try:
        named = path.lstat()
    except FileNotFoundError:
        yield _Proof()
        return
    if (os.name == 'nt' or not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
            or named.st_uid != _uid() or stat.S_IMODE(named.st_mode) != 0o600):
        _refuse()
    fd = open_hold(path)
    if fd is None:
        _refuse()
    try:
        if _version(os.fstat(fd)) != _version(named) or named.st_size > _LIMIT:
            _refuse()
        raw = b''
        while len(raw) <= _LIMIT:
            chunk = os.read(fd, _LIMIT + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
        if len(raw) != named.st_size:
            _refuse()
        try:
            record = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            _refuse()
        if not isinstance(record, dict):
            _refuse()
        proof = _Proof(record, fd, _version(named))
        proof.check()
        yield proof
        proof.check()
    finally:
        os.close(fd)


def _fsync_parent(path):
    fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
                 | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_record(fd, record):
    raw = json.dumps(record, sort_keys=True, allow_nan=False).encode('utf-8')
    if len(raw) > _LIMIT:
        _refuse()
    os.lseek(fd, 0, os.SEEK_SET)
    remaining = memoryview(raw)
    while remaining:
        count = os.write(fd, remaining)
        if count <= 0:
            _refuse()
        remaining = remaining[count:]
    os.ftruncate(fd, len(raw))
    os.fsync(fd)


def _write_checkpoint(record, check):
    from hermes_cli.startup_hold_reconcile import _version
    from pm.lifetime_handles import open_hold
    path = get_default_hermes_root() / _FILENAME
    fd = open_hold(path, create=True)
    if fd is None:
        _refuse()
    try:
        os.fchmod(fd, 0o600)
        record['phase'] = 'preparing'
        _write_record(fd, record)
        _fsync_parent(path)
        check()
        record['phase'] = 'committed'
        _write_record(fd, record)
        _fsync_parent(path)
        if (_version(os.fstat(fd)) != _version(path.lstat())
                or stat.S_IMODE(os.fstat(fd).st_mode) != 0o600):
            _refuse()
    finally:
        os.close(fd)


def prepare(*, ledger_sha256, entry_sha256, install, spawner):
    """Explicit operator write of a prospective checkpoint; never reboot or alter ledger."""
    from gateway.drain_startup import _checkout_custody
    from gateway.drain_kanban import _marker_lock, _quiescent_profiles
    from hermes_cli.kanban_db_connect import _dispatch_host_admission_lock
    from hermes_cli.process_ledger_lock import ledger_gate
    from hermes_cli import startup_hold
    from hermes_cli.startup_hold_writers import refuse_live_writers
    _paused()
    path = get_default_hermes_root() / _FILENAME
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        # Refusal only: retain writer-debt precedence without creating PM state.
        with startup_hold.writer_exclusion():
            refuse_live_writers()
            raise FileExistsError('prospective checkpoint already exists')
    with _checkout_custody(stable_identity=True), _dispatch_host_admission_lock(strict=True) as acquired:
        if not acquired:
            raise RuntimeError('startup_hold_dispatch_busy')
        with startup_hold.admission_gate(), startup_hold.writer_exclusion(), _marker_lock(), _quiescent_profiles(), ledger_gate():
            refuse_live_writers()
            identity = _native_identity()
            binding, entries = _binding()
            if not _digest(ledger_sha256) or binding['ledger']['sha256'] != ledger_sha256:
                _refuse()
            _refuse_uncertain(entries)
            _selected(entries, entry_sha256, install, spawner)
            record = dict(schema=2, kind='prospective_local_reboot', id=uuid.uuid4().hex,
                          binding=binding, host=identity['host'], boot_before=identity['boot'],
                          entries=entry_sha256, install=install, spawner=spawner)
            _paused()
            if _binding()[0] != binding or _native_identity() != identity:
                _refuse()
            def check():
                _paused()
                if _binding()[0] != binding or _native_identity() != identity:
                    _refuse()
            _write_checkpoint(record, check)
            return dict(checkpoint_id=record['id'], reboot_required=True, rows=len(entry_sha256),
                        admission_granted=False, ledger_changed=False)
