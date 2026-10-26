"""Stdlib-only startup policy; a hold never grants a task or provider turn.

Imported before dependency activation/recovery. Standalone writers publish an
OS-held reference under the same gate used by hold transactions. Uncertain
departures retain their record rather than being inferred safe from PID death.
"""
from __future__ import annotations

from contextlib import contextmanager
import atexit
import json
import os
from pathlib import Path
import stat
import sys
import threading
import uuid

from hermes_constants import get_default_hermes_root

REASON = 'startup_hold'
MARKER_FILENAME = '.drain_request.json'
JOURNAL_FILENAME = '.startup-hold.json'
_latched: set[str] = set()
_process_writers = []
_uncertain_children: set[str] = set()


def marker_path():
    return get_default_hermes_root() / MARKER_FILENAME


def _read_json(path, limit=1_048_576):
    """Absent is None; unreadable, nonregular or malformed is a fail-closed hold."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    except FileNotFoundError:
        return {} if path.is_symlink() else None
    except OSError:
        return {}
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {}
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            return {}
        value = json.loads(raw.decode('utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}
    finally:
        os.close(fd)


def journal_path(home=None):
    return get_default_hermes_root(home=home) / JOURNAL_FILENAME


def journal_present(home=None):
    try:
        journal_path(home).lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def read_journal():
    return _read_json(journal_path(), limit=4_194_304)


def read_marker():
    return _read_json(marker_path())


def read_marker_bytes():
    try:
        fd = os.open(marker_path(), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    except FileNotFoundError:
        if marker_path().is_symlink():
            raise RuntimeError('startup_hold_unavailable') from None
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError('startup_hold_unavailable')
        raw = os.read(fd, 1_048_577)
        if len(raw) > 1_048_576:
            raise RuntimeError('startup_hold_unavailable')
        return raw
    finally:
        os.close(fd)


def active(runner=None):
    if getattr(runner, '_startup_hold', False) or str(get_default_hermes_root().resolve()) in _latched:
        return True
    if journal_present():
        return True
    value = read_marker()
    return value is not None and (not value or REASON in value)


def latch_boot():
    if active():
        _latched.add(str(get_default_hermes_root().resolve()))


def _command(argv):
    from hermes_cli._parser import command_argv
    return command_argv(list(argv))


def defer_maintenance(argv=None):
    command = _command(sys.argv[1:] if argv is None else argv)
    return (active() or command[:2] == ['gateway', 'ensure']
            or command[:3] in (['gateway', 'drain', 'hold'], ['gateway', 'drain', 'reconcile'])
            or (command[:3] == ['gateway', 'drain', 'restore'] and '--hold-id' in command))


def enter_boot(project_root, argv):
    """Latch before any startup effects; unsupported held commands refuse without repair."""
    held = active()
    command = _command(argv)
    administrative = (command[:3] in (['gateway', 'drain', 'hold'], ['gateway', 'drain', 'reconcile'])
                      or (command[:3] == ['gateway', 'drain', 'restore'] and '--hold-id' in command))
    if not held and not administrative and command[:2] != ['gateway', 'ensure']:
        return False
    if held:
        _latched.add(str(get_default_hermes_root().resolve()))
        allowed = (command[:2] in (['gateway', 'run'], ['gateway', 'ensure'], ['gateway', 'status'])
                   or command[:3] in (['gateway', 'drain', 'hold'], ['gateway', 'drain', 'status'])
                   or administrative)
        if not allowed or any(token.startswith('--') and any(flag.startswith(token.split('=', 1)[0])
                for flag in ('--replace', '--force')) for token in command):
            raise SystemExit('startup_hold: only native owner/control operations are available')
    if recovery_pending(project_root):
        raise SystemExit('startup_hold: pending recovery requires a separate operator action')
    return True


def recovery_pending(project_root):
    from hermes_cli._early_recovery import interrupted_pull_marker, ZIP_SWAP_JOURNAL
    root = Path(project_root)
    paths = [root / ZIP_SWAP_JOURNAL, interrupted_pull_marker(root),
             root / '.update-incomplete', root / '.lazy-refresh-incomplete']
    from pm.environments import install_state_dir
    paths.extend(install_state_dir(root) / name for name in ('.repair-incomplete', 'source-completion-pending', 'publication.json'))
    return any(path.exists() or path.is_symlink() for path in paths)


@contextmanager
def admission_gate(*, wait=False):
    """Stable coordination inode: controls fail fast; startup may wait at most five seconds."""
    from pm.filesystem import lock_fd
    from hermes_dir_acl import make_user_dir
    home = get_default_hermes_root()
    make_user_dir(home, mode=0o700, parents=True, exist_ok=True)
    path = home / '.startup-hold-admission.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (path.stat().st_dev, path.stat().st_ino)
                or not lock_fd(fd, wait=wait, timeout=5.0 if wait else 0.0)):
            raise RuntimeError('startup_hold_writers_busy')
        yield
    finally:
        os.close(fd)


def _writer_directory():
    return get_default_hermes_root() / '.startup-writers'


def _write_reference(fd, state):
    birth = None
    if state in {'quiescent', 'exit_ready'}:
        from hermes_cli.process_identity import _process_create_time
        birth = _process_create_time()
        if birth is None:
            return  # retain unsettled state when incarnation identity is unavailable
    value = json.dumps({'schema': 1, 'pid': os.getpid(), 'create_time': birth, 'state': state}).encode()
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, value)
    os.ftruncate(fd, len(value))
    os.fsync(fd)


def _writer_quiescent():
    """A clean departure needs actual empty child/thread inventories; unknown retains debt."""
    if any(thread is not threading.current_thread() and thread.is_alive() for thread in threading.enumerate()):
        return False
    return _children_quiescent()


def _children_quiescent():
    if str(get_default_hermes_root().resolve()) in _uncertain_children:
        return False
    try:
        import psutil
        try:
            return not psutil.Process().children(recursive=True)
        except psutil.Error:
            return False
    except (ImportError, OSError, RuntimeError):
        return False


@contextmanager
def writer_scope(*, native=False):
    from pm.lifetime_handles import open_hold
    from hermes_dir_acl import make_user_dir
    with admission_gate(wait=True):
        if active() and not native:
            raise RuntimeError('startup_hold_writers_busy')
        directory = _writer_directory()
        make_user_dir(directory, mode=0o700, parents=True, exist_ok=True)
        path = directory / (uuid.uuid4().hex + '.hold')
        fd = open_hold(path, create=True)
        if fd is None:
            raise RuntimeError('startup_hold_writers_busy')
        try:
            _write_reference(fd, 'unsettled')
        except BaseException:
            os.close(fd)
            raise
    with _writer_lifetime(fd, path):
        yield fd


def _owns_writer_reference(fd, path, identity):
    from pm.filesystem import lock_fd
    from pm.lifetime_handles import open_hold
    try:
        opened, named = os.fstat(fd), path.lstat()
        if (path.parent.is_symlink() or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode) or opened.st_nlink != 1 or named.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != identity
                or (named.st_dev, named.st_ino) != identity):
            return False
        probe = open_hold(path)
        if probe is not None:
            os.close(probe)
            return False  # An unlocked reopen is not the original held description.
        # Windows leases already use native sharing exclusion, not PM byte locks.
        return os.name == 'nt' or lock_fd(fd, wait=False)
    except (ImportError, OSError, RuntimeError):
        return False


@contextmanager
def _writer_lifetime(fd, path):
    info = os.fstat(fd)
    identity = info.st_dev, info.st_ino
    root = str(path.parent.parent.resolve())
    try:
        yield fd
    finally:
        if _owns_writer_reference(fd, path, identity):
            try:
                if _writer_quiescent():
                    _write_reference(fd, 'quiescent')
            finally:
                if _owns_writer_reference(fd, path, identity):
                    os.close(fd)  # No unlock: an inherited reference may remain.
                else:
                    _uncertain_children.add(root)
        else:
            _uncertain_children.add(root)  # Never write or close a reused foreign descriptor.


@contextmanager
def writer_exclusion():
    """Caller holds admission_gate through enumeration, probing and marker publication."""
    from pm.lifetime_handles import open_hold
    descriptors = []
    covered = set()
    try:
        directory = _writer_directory()
        if directory.is_symlink():
            raise RuntimeError('startup_hold_writers_busy')
        if directory.exists():
            if not directory.is_dir():
                raise RuntimeError('startup_hold_writers_busy')
            for path in directory.iterdir():
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise RuntimeError('startup_hold_writers_busy')
                fd = open_hold(path)
                if fd is None:
                    raise RuntimeError('startup_hold_writers_busy')
                descriptors.append(fd)
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise RuntimeError('startup_hold_writers_busy')
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    record = json.loads(os.read(fd, 1024))
                except (ValueError, UnicodeError):
                    raise RuntimeError('startup_hold_writers_busy') from None
                if not isinstance(record, dict) or record.get('schema') != 1 or record.get('state') not in {'quiescent', 'exit_ready'}:
                    raise RuntimeError('startup_hold_writers_busy')
                pid, birth = record.get('pid'), record.get('create_time')
                if type(pid) is not int or pid <= 0 or type(birth) not in (int, float):
                    raise RuntimeError('startup_hold_writers_busy')
                from hermes_cli.process_identity import _pid_alive_matches
                if _pid_alive_matches(pid, birth, strict=True) is not False:
                    raise RuntimeError('startup_hold_writers_busy')
                covered.add((pid, birth))
        yield covered
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _process_writer_purpose(argv):
    command = _command(argv)
    if command[:2] == ['gateway', 'run']:
        return 'gateway'
    return command[0] if command[:1] in (['serve'], ['dashboard']) else None


def _writer_reference_path(fd):
    info = os.fstat(fd)
    directory = get_default_hermes_root().resolve() / '.startup-writers'
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError('startup_writer_handoff_unavailable')
    matches = []
    for path in directory.iterdir():
        named = path.lstat()
        if (stat.S_ISREG(named.st_mode) and named.st_nlink == 1
                and (named.st_dev, named.st_ino) == (info.st_dev, info.st_ino)):
            matches.append(path)
    if len(matches) != 1:
        raise RuntimeError('startup_writer_handoff_unavailable')
    path = matches[0]
    try:
        valid = path.suffix == '.hold' and uuid.UUID(path.stem).hex == path.stem
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError('startup_writer_handoff_unavailable')
    return path


def _retain_process_writer(scope, native, purpose, *, path=None):
    fd = scope.__enter__()
    path = _writer_reference_path(fd) if path is None else path
    info = os.fstat(fd)
    identity = info.st_dev, info.st_ino
    if not _owns_writer_reference(fd, path, identity):
        raise RuntimeError('startup_writer_handoff_unavailable')
    _process_writers.append((scope, fd, native, purpose, path, identity))
    # Registered before PM/plugins: runs after their cleanup, retains the raw
    # reference until this last callback and actual process departure.
    atexit.register(scope.__exit__, None, None, None)


def register_process_writer(argv):
    """Reserve before a supported standalone entry can perform any startup effects."""
    purpose = _process_writer_purpose(argv)
    if purpose is None or _process_writers:
        return
    native = purpose == 'gateway'
    _retain_process_writer(writer_scope(native=native), native, purpose)


def mark_native_exit_ready():
    """The next instruction exits the process; retain the OS reference through that exit.

    Threads die with the process. External descendants need separate exit proof,
    so opaque factories or a nonempty/unknown child inventory retain unsettled debt.
    Exclusion also requires this exact process incarnation to have departed.
    """
    if _children_quiescent():
        for _scope, fd, native, _purpose, path, identity in _process_writers:
            if native:
                if _owns_writer_reference(fd, path, identity):
                    _write_reference(fd, 'exit_ready')
                else:
                    _uncertain_children.add(str(path.parent.parent))


def mark_uncertain_children():
    """Opaque child factories cannot provide complete descendant exit evidence."""
    _uncertain_children.add(str(get_default_hermes_root().resolve()))
