"""Same-incarnation POSIX re-exec of an already reserved startup writer."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import threading
import uuid

_ATTRIBUTE = '_hermes_startup_writer_handoff'
_LIMIT = 16_384


def _hex(value):
    return isinstance(value, str) and len(value) == 32 and all(c in '0123456789abcdef' for c in value)


def _identity(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError('startup_writer_handoff_unavailable')
    return info.st_dev, info.st_ino


def _read(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    raw = os.read(fd, _LIMIT + 1)
    if len(raw) > _LIMIT:
        raise RuntimeError('startup_writer_handoff_unavailable')
    return raw


def _write(fd, raw):
    if len(raw) > _LIMIT:
        raise RuntimeError('startup_writer_handoff_unavailable')
    os.lseek(fd, 0, os.SEEK_SET)
    if os.write(fd, raw) != len(raw):
        raise RuntimeError('startup_writer_handoff_unavailable')
    os.ftruncate(fd, len(raw))
    os.fsync(fd)


def _capability():
    from hermes_cli import startup_hold as policy
    if (threading.current_thread() is not threading.main_thread()
            or any(t is not threading.current_thread() and t.is_alive() for t in threading.enumerate())
            or hasattr(sys, _ATTRIBUTE) or len(policy._process_writers) != 1):
        raise RuntimeError('startup_writer_handoff_unavailable')
    _scope, fd, native, purpose, path, identity = policy._process_writers[0]
    root = policy.get_default_hermes_root().resolve()
    name = path.name
    if (path.parent != root / '.startup-writers' or _identity(fd) != identity
            or not policy._owns_writer_reference(fd, path, identity)):
        raise RuntimeError('startup_writer_handoff_unavailable')
    if policy._process_writer_purpose(sys.argv[1:]) != purpose:
        raise RuntimeError('startup_writer_handoff_unavailable')
    return dict(schema=1, state='exec_pending', pid=os.getpid(), create_time=None,
                fd=fd, root=str(root), name=name, device=identity[0], inode=identity[1],
                native=native, purpose=purpose, nonce=uuid.uuid4().hex,
                uncertain=str(root) in policy._uncertain_children)


def _prelude(capability, body):
    body_length = len(body)
    digest = hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()
    # Runs before the original relaunch body or any recoverable-checkout imports.
    return f'''import hashlib, os, stat, sys
def _hermes_restore_startup_writer():
    info = os.fstat({capability['fd']!r})
    if (os.getpid() != {capability['pid']!r}
            or (info.st_dev, info.st_ino) != {(capability['device'], capability['inode'])!r}
            or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not os.get_inheritable({capability['fd']!r})):
        raise RuntimeError('startup_writer_handoff_unavailable')
    if (len(sys.orig_argv) < 2 or sys.orig_argv[-2] != '-c'
            or not isinstance(sys.orig_argv[-1], str)
            or hashlib.sha256(sys.orig_argv[-1][-{body_length!r}:].encode('utf-8', 'surrogatepass')).hexdigest() != {digest!r}):
        raise RuntimeError('startup_writer_handoff_unavailable')
    os.set_inheritable({capability['fd']!r}, False)
    # Remove only this transport so a later -c relaunch cannot replay the old nonce.
    sys.orig_argv[-1] = sys.orig_argv[-1][-{body_length!r}:]
    setattr(sys, {_ATTRIBUTE!r}, {capability!r})
_hermes_restore_startup_writer()
del _hermes_restore_startup_writer
'''


def _restore(fd, path, identity, raw, inherited, position):
    from hermes_cli import startup_hold as policy
    try:
        if not policy._owns_writer_reference(fd, path, identity):
            raise RuntimeError('startup writer descriptor changed')
        os.set_inheritable(fd, inherited)
        _write(fd, raw)
        os.lseek(fd, position, os.SEEK_SET)
    except (OSError, RuntimeError) as error:
        # An uncertain rollback must not reach bootstrap's ordinary degrade-and-continue path.
        policy._uncertain_children.add(str(path.parent.parent))
        raise SystemExit('startup_writer_handoff_restore_failed') from error


def _validate_previous(record):
    if (not isinstance(record, dict) or set(record) != {'schema', 'pid', 'create_time', 'state'}
            or type(record['schema']) is not int or record['schema'] != 1
            or type(record['pid']) is not int or record['pid'] != os.getpid()
            or record['create_time'] is not None or record['state'] != 'unsettled'):
        raise RuntimeError('startup_writer_handoff_unavailable')


@contextmanager
def writer_exec_handoff(command):
    """Keep the same locked writer reference across exec; restore exactly if exec fails."""
    from hermes_cli import startup_hold as policy
    if not policy._process_writers:
        yield command
        return
    if (os.name != 'posix' or len(command) < 2 or command[-2] != '-c'
            or not isinstance(command[-1], str) or not command[-1]):
        raise RuntimeError('startup_writer_handoff_unavailable')
    with policy.admission_gate(wait=True):
        capability = _capability()
        fd = capability['fd']
        position, inherited = os.lseek(fd, 0, os.SEEK_CUR), os.get_inheritable(fd)
        previous = _read(fd)
        record = json.loads(previous)
        _validate_previous(record)
    identity = capability['device'], capability['inode']
    try:
        _write(fd, json.dumps(capability).encode('ascii'))
        os.set_inheritable(fd, True)
        yield [*command[:-1], _prelude(capability, command[-1]) + command[-1]]
    finally:
        _restore(fd, Path(capability['root']) / '.startup-writers' / capability['name'],
                 identity, previous, inherited, position)


def _validate_shape(capability):
    keys = {'schema', 'state', 'pid', 'create_time', 'fd', 'root', 'name', 'device',
            'inode', 'native', 'purpose', 'nonce', 'uncertain'}
    if not isinstance(capability, dict) or set(capability) != keys:
        raise RuntimeError('startup_writer_handoff_unavailable')
    if (type(capability['schema']) is not int or capability['schema'] != 1 or capability['state'] != 'exec_pending'
            or capability['create_time'] is not None or not _hex(capability['nonce'])
            or not isinstance(capability['name'], str) or not capability['name'].endswith('.hold')
            or not _hex(capability['name'][:-5])):
        raise RuntimeError('startup_writer_handoff_unavailable')
    if (any(type(capability[k]) is not int or capability[k] < 0 for k in ('pid', 'fd', 'device', 'inode'))
            or capability['pid'] != os.getpid() or capability['fd'] <= 2
            or type(capability['native']) is not bool or type(capability['uncertain']) is not bool):
        raise RuntimeError('startup_writer_handoff_unavailable')


def _validate_reference(capability):
    from hermes_cli import startup_hold as policy
    _validate_shape(capability)
    root = policy.get_default_hermes_root().resolve()
    purpose = policy._process_writer_purpose(sys.argv[1:])
    if (capability['root'] != str(root) or capability['purpose'] != purpose
            or capability['native'] != (purpose == 'gateway') or purpose is None
            or policy._process_writers):
        raise RuntimeError('startup_writer_handoff_unavailable')
    directory = root / '.startup-writers'
    identity = capability['device'], capability['inode']
    fd = capability['fd']
    if _identity(fd) != identity or os.get_inheritable(fd):
        raise RuntimeError('startup_writer_handoff_unavailable')
    if json.loads(_read(fd)) != capability:
        raise RuntimeError('startup_writer_handoff_unavailable')
    if not policy._owns_writer_reference(fd, directory / capability['name'], identity):
        raise RuntimeError('startup_writer_handoff_unavailable')
    return fd


def adopt_process_writer():
    """Consume one same-process capability before ordinary registration or PM effects."""
    from hermes_cli import startup_hold as policy
    capability = getattr(sys, _ATTRIBUTE, None)
    if capability is None:
        return
    if os.name != 'posix':
        raise RuntimeError('startup_writer_handoff_unavailable')
    with policy.admission_gate(wait=True):
        fd = _validate_reference(capability)
        policy._write_reference(fd, 'unsettled')
        if capability['uncertain']:
            policy._uncertain_children.add(capability['root'])
        delattr(sys, _ATTRIBUTE)
        path = Path(capability['root']) / '.startup-writers' / capability['name']
        policy._retain_process_writer(policy._writer_lifetime(fd, path), capability['native'],
                                      capability['purpose'], path=path)
