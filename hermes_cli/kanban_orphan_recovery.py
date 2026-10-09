"""Exact, journaled maintenance of orphan runs on terminal cards.

This module never dispatches, changes a card, or signals a process. The explicit
home and reviewed manifest are the authority; ambient board pins cannot redirect
maintenance. Run ``python -m hermes_cli.kanban_orphan_recovery --help``.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import time
import uuid

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.sqlite_safe_read import connect_tracked

RECOVERED = 'orphan_run_reconciled'
UNDONE = 'orphan_run_reconciliation_undone'
CHANGED_FIELDS = ('status', 'outcome', 'ended_at')
BACKUP_TIMEOUT_SECONDS = 30


class RecoveryRefused(RuntimeError):
    """A reviewed precondition cannot be established; no bypass is provided."""


def digest(value) -> str:
    """Digest exact SQLite values, including their JSON scalar types."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def _require(condition, message):
    if not condition:
        raise RecoveryRefused(message)


def _context_guard():
    from agent.delegation_context import explicit_board_intent_is_pinned
    _require(not explicit_board_intent_is_pinned(), 'Worker/delegated context cannot perform cross-board maintenance')


def _path(home, board):
    _require(kb._normalize_board_slug(board) == board, 'An explicit canonical board slug is required')
    path = home / 'kanban.db' if board == 'default' else home / 'kanban/boards' / board / 'kanban.db'
    path = path.resolve(strict=True)
    _require(path.is_relative_to(home) and path.is_file(), 'Board database escapes the explicit home or is absent')
    return path


def _identity(path):
    stat = path.stat()
    return {'path': str(path), 'device': stat.st_dev, 'inode': stat.st_ino}


def _connect(path, mode):
    conn = connect_tracked(path.as_uri() + f'?mode={mode}', uri=True, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _schema(conn):
    _require(not conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('task_runs','task_events')").fetchone(),
             'Unexpected triggers on maintenance tables')
    required = {'tasks': {'id', 'status', 'current_run_id', 'claim_lock', 'claim_expires', 'worker_pid'},
                'task_runs': {'id', 'task_id', 'status', 'outcome', 'ended_at', 'worker_pid', 'worker_started_at', 'claim_lock', 'claim_expires'},
                'task_events': {'task_id', 'run_id', 'kind', 'payload', 'created_at'}}
    for table, columns in required.items():
        actual = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
        _require(columns <= actual, f'Unsupported existing {table} schema; maintenance does not migrate')
    return digest([list(row) for row in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')])


def _own_audit(event, operation):
    if event['kind'] not in (RECOVERED, UNDONE):
        return False
    try:
        return json.loads(event['payload']).get('operation_id') == operation
    except (TypeError, ValueError, AttributeError):
        return False


def _capture(conn, task_id, run_id, operation):
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    run = conn.execute('SELECT * FROM task_runs WHERE id=? AND task_id=?', (run_id, task_id)).fetchone()
    _require(task is not None and run is not None, 'Exact task/run pair is absent')
    others = [dict(row) for row in conn.execute('SELECT * FROM task_runs WHERE task_id=? AND id<>? ORDER BY id', (task_id, run_id))]
    events = [dict(row) for row in conn.execute('SELECT * FROM task_events WHERE task_id=? ORDER BY id', (task_id,))]
    history = {'runs': others, 'events': [event for event in events
                                        if not (_own_audit(event, operation) and event['run_id'] == run_id)]}
    return {'task': dict(task), 'run': dict(run), 'history': history}, [event for event in events if _own_audit(event, operation) and event['run_id'] == run_id]


def _fingerprint_parts(value):
    """Recognize legacy numeric births and canonical boot-ID/PID-1 epochs."""
    if type(value) is int:
        return ('', value) if value > 0 else None
    if not isinstance(value, str) or value.count('|') > 1:
        return None
    epoch, start = value.split('|') if '|' in value else ('', value)
    if not start.isascii() or not start.isdecimal() or int(start) <= 0:
        return None
    if epoch:
        if epoch.count(':') != 1:
            return None
        boot, pid1 = epoch.split(':')
        if not boot and not pid1:
            return None
        if boot:
            try:
                if str(uuid.UUID(boot)) != boot:
                    return None
            except ValueError:
                return None
        if pid1 and (not pid1.isascii() or not pid1.isdecimal() or int(pid1) <= 0):
            return None
    return epoch, int(start)


def worker_state(run):
    """Positive absence/reuse evidence; missing process access remains unknown."""
    import psutil
    recorded = _fingerprint_parts(run['worker_started_at'])
    if recorded is None or type(run['worker_pid']) is not int or run['worker_pid'] <= 0:
        return 'unknown'
    try:
        process = psutil.Process(run['worker_pid'])
        process.create_time()  # AccessDenied is distinct from NoSuchProcess.
        from hermes_cli.kanban_db_dispatch import _process_fingerprint
        from gateway.status import start_time_fingerprints_match
        current = _fingerprint_parts(_process_fingerprint(run['worker_pid']))
        if current is None:
            return 'unknown'
        epoch, start = recorded
        current_epoch, current_start = current
        if epoch and epoch != current_epoch:
            # A missing/partial current witness cannot prove another incarnation.
            if not current_epoch:
                return 'unknown'
            old_boot, old_pid1 = epoch.split(':')
            new_boot, new_pid1 = current_epoch.split(':')
            if old_boot and new_boot and old_boot != new_boot:
                return 'reused'
            if old_pid1 and new_pid1 and old_pid1 != new_pid1:
                return 'reused'
            return 'unknown'
        return 'live' if start_time_fingerprints_match(start, current_start) else 'reused'
    except psutil.NoSuchProcess:
        return 'absent'
    except (psutil.AccessDenied, OSError, ValueError, TypeError):
        return 'unknown'


def _guard(view, claim_hosts, probe):
    task, run = view['task'], view['run']
    _require(task['status'] in ('done', 'archived'), 'Card must already be terminal')
    _require(all(task.get(key) is None for key in ('current_run_id', 'claim_lock', 'claim_expires', 'worker_pid')),
             'Card has current run/claim/worker activity')
    # Normal completion/archive can retain a birth marker after clearing the PID.
    # Preserve this historical field in the task preimage, never infer a live owner.
    task_birth = task.get('worker_started_at')
    _require(task_birth is None or _fingerprint_parts(task_birth) is not None, 'Task worker birth marker is malformed')
    _require(all(other['ended_at'] is not None and other.get('worker_pid') is None
                 for other in view['history']['runs']), 'Another task run is open or retains a worker PID')
    _require(run['status'] == 'running' and run['ended_at'] is None and run['outcome'] is None, 'Run is not an exact open orphan')
    expiry = run['claim_expires']
    _require(type(expiry) is int and 0 < expiry < time.time(), 'Lease is unexpired or unknown')
    claim = run['claim_lock']
    _require(isinstance(claim, str) and ':' in claim and claim.rsplit(':', 1)[0] in claim_hosts, 'Claim host is foreign/unreviewed')
    pid = run['worker_pid']
    _require(type(pid) is int and pid > 0 and _fingerprint_parts(run['worker_started_at']) is not None,
             'Worker PID/birth identity is unknown')
    _require(probe(run) in ('absent', 'reused'), 'Worker identity is live or unknown')


def prepare(home: Path, selection: list[dict], *, worker_probe=worker_state, claim_hosts=()) -> dict:
    """Logical read-only preview; capture existing closed history rather than discard it."""
    _context_guard()
    home = Path(home).resolve(strict=True)
    _require(bool(selection), 'Explicit nonempty run selection is required')
    plan = {'version': 1, 'operation_id': uuid.uuid4().hex, 'home': str(home), 'host': socket.gethostname(),
            'claim_hosts': sorted(set((socket.gethostname(), *claim_hosts))), 'boards': []}
    grouped = {}
    for item in selection:
        _require(set(item) == {'board', 'task_id', 'run_id'} and type(item['run_id']) is int and item['run_id'] > 0,
                 'Selection must contain exact board/task/run IDs')
        grouped.setdefault(item['board'], []).append(item)
    seen = set()
    for board, items in sorted(grouped.items()):
        path = _path(home, board)
        identity = _identity(path)
        key = (identity['device'], identity['inode'])
        _require(key not in seen, 'Boards alias the same physical database')
        seen.add(key)
        entry = {'board': board, 'database': identity, 'rows': []}
        with closing(_connect(path, 'ro')) as conn:
            conn.execute('BEGIN')
            entry['schema_digest'] = _schema(conn)
            for item in items:
                _require(item['run_id'] not in {r['run']['id'] for r in entry['rows']}, 'Duplicate selected run')
                view, _ = _capture(conn, item['task_id'], item['run_id'], plan['operation_id'])
                _guard(view, plan['claim_hosts'], worker_probe)
                entry['rows'].append(view)
            _require(identity == _identity(path), 'Database identity changed during preview')
        plan['boards'].append(entry)
    digest(plan)  # Refuse unsupported non-JSON/BLOB preimages rather than normalize them.
    return plan


def _sync_directory(path):
    if os.name != 'nt':
        directory = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _save_journal(path, journal):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf8') as stream:
        json.dump(journal, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


def _postimage(view, timestamp):
    return dict(view['run'], status='reclaimed', outcome='reclaimed', ended_at=timestamp)


def _audit_payload(plan, view, post):
    return {'operation_id': plan['operation_id'], 'plan_digest': digest(plan), 'administrative': True,
            'preimage_digest': digest(view['run']), 'postimage_digest': digest(post),
            'meaning': 'Expired orphan bookkeeping; not evidence of successful worker execution'}


def _validate_entry(conn, entry, plan, timestamp, probe):
    _require(_schema(conn) == entry['schema_digest'], 'Database schema changed')
    states = []
    for expected in entry['rows']:
        current, audits = _capture(conn, expected['task']['id'], expected['run']['id'], plan['operation_id'])
        _require(digest(current['task']) == digest(expected['task']) and digest(current['history']) == digest(expected['history']),
                 'Task or successor/history preimage changed')
        _guard(expected, plan['claim_hosts'], probe)  # Re-probe original worker at each execution.
        post = _postimage(expected, timestamp)
        payload = _audit_payload(plan, expected, post)
        _require(all(json.loads(event['payload']) == payload for event in audits), 'Audit provenance changed')
        kinds = [event['kind'] for event in audits]
        if current['run'] == expected['run'] and not kinds:
            states.append('pending')
        elif current['run'] == post and kinds == [RECOVERED]:
            states.append('applied')
        elif current['run'] == expected['run'] and kinds == [RECOVERED, UNDONE]:
            states.append('undone')
        else:
            raise RecoveryRefused('Run pre/postimage or audit CAS changed')
    _require(len(set(states)) == 1, 'Partial row state within one board violates transaction atomicity')
    return states[0]


def _open_boards(stack, plan, mode):
    _context_guard()
    _require(plan.get('version') == 1 and plan['host'] == socket.gethostname(), 'Manifest version/host mismatch')
    home = Path(plan['home']).resolve(strict=True)
    result = {}
    seen = set()
    _require(isinstance(plan.get('boards'), list) and bool(plan['boards']), 'Manifest has no selected boards')
    for entry in plan['boards']:
        _require(entry['board'] not in result and bool(entry['rows']), 'Duplicate or empty selected board')
        path = _path(home, entry['board'])
        identity = _identity(path)
        _require(identity == entry['database'], 'Board database was redirected or replaced')
        key = (identity['device'], identity['inode'])
        _require(key not in seen, 'Boards alias one database')
        seen.add(key)
        conn = stack.enter_context(closing(_connect(path, mode)))
        _require(identity == _identity(path), 'Database changed while opening')
        result[entry['board']] = conn
    return result


def _backup(conn, path, entry, plan, timestamp, probe):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(_connect(path, 'rw')) as target:
        deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS
        def progress(status, remaining, total):
            _require(time.monotonic() <= deadline, 'SQLite backup exceeded its bounded deadline')
        # SQLite's connection busy handler runs before backup's progress callback.
        # Disable it only for this copy so BUSY reaches the bounded retry loop.
        busy_timeout = conn.execute('PRAGMA busy_timeout').fetchone()[0]
        conn.execute('PRAGMA busy_timeout=0')
        try:
            conn.backup(target, pages=128, progress=progress, sleep=0.05)
        finally:
            conn.execute(f'PRAGMA busy_timeout={int(busy_timeout)}')
        _require(target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Backup integrity failed')
        _require(_validate_entry(target, entry, plan, timestamp, probe) == 'pending', 'Backup preimage changed')
    with path.open('r+b') as stream:
        os.fsync(stream.fileno())
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _validate_backups(journal_path, journal, probe):
    """Require the complete retained intent and exact pending board snapshots."""
    plan = journal['plan']
    boards = plan.get('boards')
    _require(isinstance(boards, list) and bool(boards), 'Manifest has no selected boards')
    names = [entry['board'] for entry in boards]
    _require(len(set(names)) == len(names), 'Duplicate selected board')
    backups = journal.get('backups')
    _require(isinstance(backups, dict) and set(backups) == set(names), 'Retained backup coverage is incomplete')
    timestamp = journal.get('timestamp')
    _require(type(timestamp) is int and 0 < timestamp <= time.time(), 'Invalid administrative timestamp')
    directory = journal_path.parent.resolve(strict=True)
    _require(not directory.is_relative_to(Path(plan['home']).resolve()), 'Backups must remain outside the board home')
    try:
        for entry in boards:
            board = entry['board']
            _require(kb._normalize_board_slug(board) == board and bool(entry['rows']), 'Invalid selected board')
            backup = backups[board]
            expected_path = directory / (board + '.sqlite3')
            path = Path(backup['path'])
            _require(not path.is_symlink() and path.resolve(strict=True) == expected_path,
                     'Retained backup path is redirected')
            _require(hashlib.sha256(path.read_bytes()).hexdigest() == backup['sha256'], 'Retained backup changed')
            with closing(_connect(path, 'ro')) as conn:
                conn.execute('BEGIN')
                _require(conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Retained backup integrity failed')
                _require(_validate_entry(conn, entry, plan, timestamp, probe) == 'pending', 'Retained backup preimage changed')
    except (OSError, sqlite3.Error) as error:
        raise RecoveryRefused('Retained backup cannot be verified') from error


def _update(conn, before, after):
    columns = list(before)
    quote = lambda name: '"' + name.replace('"', '""') + '"'
    where = ' AND '.join(quote(key) + ' IS ?' for key in columns)
    changed = conn.execute('UPDATE task_runs SET status=?,outcome=?,ended_at=? WHERE ' + where,
                           (*[after[key] for key in CHANGED_FIELDS], *[before[key] for key in columns]))
    _require(changed.rowcount == 1, 'Exact row CAS failed')


def _execute(journal_path, journal, direction, probe):
    plan, timestamp = journal['plan'], journal['timestamp']
    contexts = {}
    with ExitStack() as stack:
        conns = _open_boards(stack, plan, 'rw')
        try:
            # Hold both normal writer boundaries; validate all rows before changing any.
            for board, conn in conns.items():
                cm = kbc.write_txn(conn); cm.__enter__(); contexts[board] = cm
            states = {entry['board']: _validate_entry(conns[entry['board']], entry, plan, timestamp, probe) for entry in plan['boards']}
            if direction == 'apply':
                _require('undone' not in states.values(), 'An undone operation cannot be reapplied; obtain a fresh review')
            for entry in plan['boards']:
                board = entry['board']; conn = conns[board]
                mutate = states[board] == ('pending' if direction == 'apply' else 'applied')
                if mutate:
                    for view in entry['rows']:
                        post = _postimage(view, timestamp)
                        before, after = (view['run'], post) if direction == 'apply' else (post, view['run'])
                        _update(conn, before, after)
                        kb._append_event(conn, view['task']['id'], RECOVERED if direction == 'apply' else UNDONE,
                                         _audit_payload(plan, view, post), run_id=view['run']['id'])
                cm = contexts.pop(board)
                # write_txn can raise after COMMIT. Durable row + audit classification,
                # not the filesystem state marker, is authoritative on resume/undo.
                cm.__exit__(None, None, None)
                states[board] = _validate_entry(conn, entry, plan, timestamp, probe)
                journal['states'] = dict(states)
                _save_journal(journal_path, journal)
        finally:
            for cm in reversed(list(contexts.values())):
                cm.__exit__(RuntimeError, RuntimeError('maintenance interrupted before commit'), None)
    return {'journal': str(journal_path), 'plan_digest': digest(plan), 'states': states}


def apply(plan: dict, run_dir: Path, *, expected_digest: str, worker_probe=worker_state):
    """Create WAL-consistent backups and close only approved administrative run rows."""
    _require(digest(plan) == expected_digest, 'Reviewed plan digest mismatch')
    out = Path(run_dir).absolute()
    _require(not out.is_symlink() and not out.resolve().is_relative_to(Path(plan['home']).resolve()),
             'Journal/backup directory must be outside the board home')
    journal_path = out / 'journal.json'
    if journal_path.exists():
        journal = json.loads(journal_path.read_text(encoding='utf-8-sig'))
        _require(journal['plan'] == plan, 'Run directory belongs to a different reviewed operation')
        _validate_backups(journal_path, journal, worker_probe)
    else:
        with ExitStack() as stack:
            conns = _open_boards(stack, plan, 'ro')
            timestamp = int(time.time())
            for entry in plan['boards']:
                _validate_entry(conns[entry['board']], entry, plan, timestamp, worker_probe)
            out.mkdir(mode=0o700)  # Never claim/overwrite an existing foreign allocation.
            _sync_directory(out.parent)  # Persist the fresh allocation before SQL mutations.
            backups = {entry['board']: _backup(conns[entry['board']], out/(entry['board']+'.sqlite3'), entry, plan, timestamp, worker_probe)
                       for entry in plan['boards']}
        journal = {'plan': plan, 'timestamp': timestamp, 'backups': backups, 'states': {entry['board']:'pending' for entry in plan['boards']}}
        _save_journal(journal_path, journal)  # Durable full preimage intent before any SQL mutation.
    return _execute(journal_path, journal, 'apply', worker_probe)


def undo(journal_path: Path, *, expected_digest: str, worker_probe=worker_state):
    """Restore only changed run fields after exact postimage/task/history comparison."""
    journal_path = Path(journal_path)
    journal = json.loads(journal_path.read_text(encoding='utf-8-sig'))
    _require(digest(journal['plan']) == expected_digest, 'Reviewed plan digest mismatch')
    _validate_backups(journal_path, journal, worker_probe)
    return _execute(journal_path, journal, 'undo', worker_probe)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    verbs = parser.add_subparsers(dest='verb', required=True)
    dry = verbs.add_parser('dry-run'); dry.add_argument('--home', type=Path, required=True)
    dry.add_argument('--selection', type=Path, required=True)
    dry.add_argument('--claim-host', action='append', default=[], help='Explicit reviewed historical alias of this host')
    commit = verbs.add_parser('apply'); commit.add_argument('--plan', type=Path, required=True)
    commit.add_argument('--plan-sha256', required=True); commit.add_argument('--run-dir', type=Path, required=True)
    revert = verbs.add_parser('undo'); revert.add_argument('--journal', type=Path, required=True)
    revert.add_argument('--plan-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        if args.verb == 'dry-run':
            result = prepare(args.home, json.loads(args.selection.read_text(encoding='utf-8-sig')), claim_hosts=args.claim_host)
        elif args.verb == 'apply':
            result = apply(json.loads(args.plan.read_text(encoding='utf-8-sig')), args.run_dir, expected_digest=args.plan_sha256)
        else:
            result = undo(args.journal, expected_digest=args.plan_sha256)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (RecoveryRefused, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'error': str(error), 'type': type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
