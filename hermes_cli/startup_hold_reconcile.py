"""Read-only reconciliation of legacy history against held writer references.

An assessment is never an admission capability or a descendant-closure receipt.
In particular, a parent's accepted departure cannot cover its legacy helpers.
"""
import hashlib
import json
import math
import os
import stat

from hermes_constants import get_default_hermes_root

_LIMIT = 4_194_304


def ledger_snapshot():
    """Bound bytes to one regular inode; never quarantine or repair bad history."""
    path = get_default_hermes_root() / 'spawn-ledger.json'
    try:
        named = path.lstat()
    except FileNotFoundError:
        return None, []
    except OSError:
        raise RuntimeError('startup_hold_writers_unknown') from None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                     | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    except OSError:
        raise RuntimeError('startup_hold_writers_unknown') from None
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or not stat.S_ISREG(named.st_mode)
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise RuntimeError('startup_hold_writers_unknown')
        raw = os.read(fd, _LIMIT + 1)
        if len(raw) > _LIMIT:
            raise RuntimeError('startup_hold_writers_unknown')
        entries = json.loads(raw.decode('utf-8-sig'))
    except (OSError, ValueError, UnicodeError):
        raise RuntimeError('startup_hold_writers_unknown') from None
    finally:
        os.close(fd)
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        raise RuntimeError('startup_hold_writers_unknown')
    return hashlib.sha256(raw).hexdigest(), entries


def entry_state(entry, covered):
    """Only exact existing writer coverage is accepted, never spawner ancestry."""
    from hermes_cli.startup_hold_writers import _same_root
    from hermes_cli.process_identity import _pid_alive_matches
    if entry.get('purpose') not in {'serve', 'dashboard', 'mcp-helper'}:
        return 'unrelated'
    home = entry.get('hermes_home')
    if home and not _same_root(home):
        return 'foreign'
    pid, birth = entry.get('pid'), entry.get('create_time')
    if (type(pid) is not int or pid <= 0 or type(birth) not in (int, float)
            or not math.isfinite(birth) or birth <= 0):
        return 'unknown'
    live = _pid_alive_matches(pid, birth, strict=True)
    if live is not False:
        return 'busy' if live is True else 'unknown'
    if (pid, birth) in covered:
        return 'covered'
    return 'closure_required'


def assess():
    """Reacquire all proof each time; return only counts and a snapshot commitment."""
    from agent.estop import is_engaged
    from hermes_cli import startup_hold
    from hermes_cli.startup_hold_writers import refuse_live_writers
    if not is_engaged():
        raise RuntimeError('startup_hold_requires_pause')
    with startup_hold.admission_gate(), startup_hold.writer_exclusion() as covered:
        refuse_live_writers()
        digest, entries = ledger_snapshot()
        counts = dict.fromkeys(('unrelated', 'foreign', 'unknown', 'busy', 'covered', 'closure_required'), 0)
        for entry in entries:
            counts[entry_state(entry, covered)] += 1
        if ledger_snapshot()[0] != digest:
            raise RuntimeError('startup_hold_ledger_changed')
        if not is_engaged():
            raise RuntimeError('startup_hold_requires_pause')
        return dict(schema=1, mode='assessment_only', ledger_sha256=digest,
                    counts=counts, admission_granted=False, ledger_changed=False)
