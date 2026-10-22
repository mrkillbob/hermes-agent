"""Non-destructive, explicitly requested retention of one owned worktree.

The immutable binding stays at its original identity. A private receipt fences
that identity until an explicit verified restore; unknown Git states stay held.
"""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import stat
import sqlite3
import uuid

from hermes_state_worktree_cleanup import reserve_retention


class RetentionUnknown(RuntimeError):
    """The physical or durable transition cannot be proved."""


def receipt_path(record):
    digest = hashlib.sha256(str(Path(record.worktree_path)).encode()).hexdigest()
    return Path(record.repo_common_dir) / 'hermes-conversation-retention-v1' / (digest + '.json')


def retention_pending(common, path):
    digest = hashlib.sha256(str(Path(path)).encode()).hexdigest()
    receipt = Path(common) / 'hermes-conversation-retention-v1' / (digest + '.json')
    try:
        receipt.parent.lstat()
    except FileNotFoundError:
        return False
    _private(receipt.parent)
    try:
        receipt.lstat()
    except FileNotFoundError:
        return False
    return True


def _identity(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise RetentionUnknown('directory identity unavailable')
    return [info.st_dev, info.st_ino]


def _owner_private(info):
    getuid = getattr(os, 'getuid', None)
    return getuid is None or (info.st_uid == getuid() and not stat.S_IMODE(info.st_mode) & 0o077)


def _private(path):
    if os.name == 'nt':
        from hermes_cli.windows_ssh_runtime import _ensure_directory
        _ensure_directory(path)
    else:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or not _owner_private(info):
        raise RetentionUnknown('private receipt directory unavailable')


def _sync_parent(path):
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write(path, data, *, create=False):
    _private(path.parent)
    if path.is_symlink() or (create and path.exists()):
        raise RetentionUnknown('receipt already exists')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    _write_new(temporary, data)
    if create:
        os.link(temporary, path)
        temporary.unlink()
    else:
        os.replace(temporary, path)
    _sync_parent(path)


def _write_new(path, data):
    if os.name == 'nt':
        from hermes_cli.windows_ssh_runtime import _write_new as protected_write
        protected_write(path, json.dumps(data, sort_keys=True).encode('utf-8'))
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


def _binding(record):
    return {key: value for key, value in asdict(record).items()
            if key not in {'state', 'updated_at', 'failure_phase', 'failure_message'}}


def _read(record, db, path=None):
    path = path or receipt_path(record)
    _private(path.parent)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536 or not _owner_private(info):
        raise RetentionUnknown('receipt identity unavailable')
    if os.name == 'nt':
        from hermes_cli.windows_ssh_runtime import _read_shared
        raw = _read_shared(path, 65536, 7)
        if raw is None:
            raise RetentionUnknown('receipt unavailable')
        data = json.loads(raw.decode('utf-8-sig'))
    else:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(data, dict) or data.get('version') != 1
            or data.get('binding') != _binding(record)
            or data.get('database') != str(db.db_path.resolve())
            or data.get('database_identity') != _file_identity(db.db_path)
            or data.get('phase') not in {'prepared', 'moved', 'retained', 'restoring', 'restore_moved', 'restored', 'cancelled'}):
        raise RetentionUnknown('receipt does not match binding')
    original = Path(record.worktree_path)
    target = Path(data['destination'])
    expected_name = '.hermes-retained-' + data['token']
    if target.parent != original.parent or target.name != expected_name:
        raise RetentionUnknown('receipt destination changed')
    return data


def _file_identity(path):
    info = path.stat()
    return [info.st_dev, info.st_ino]


def _admin(manager, path):
    return str(Path(manager._git_stdout(
        path, ['rev-parse', '--path-format=absolute', '--git-dir'], 'retention'
    )).resolve())


def _environment(manager, record, path):
    # JSON-normalize Paths/tuples for exact comparison with the durable receipt.
    identity = manager._environment_link_identity(record, path)
    return {"present": (path / '.venv').exists() or (path / '.venv').is_symlink(),
            "identity": json.loads(json.dumps(identity, default=str))}


def _marker(manager, path):
    from agent.conversation_worktree import _OWNER_MARKER
    marker = Path(manager._git_stdout(path, ['rev-parse', '--git-path', _OWNER_MARKER], 'retention'))
    return marker if marker.is_absolute() else path / marker


def _claims(manager, record, path):
    from agent.conversation_worktree import _common_owner_claim_path
    common = _common_owner_claim_path(Path(record.repo_common_dir), Path(record.worktree_path))
    return json.loads(common.read_text(encoding='utf-8-sig')), json.loads(_marker(manager, path).read_text(encoding='utf-8-sig'))


def _verify_claims(manager, record, path, data):
    from agent.conversation_worktree import _common_owner_claim_path
    common, marker = _claims(manager, record, path)
    projected = replace(record, worktree_path=data['destination'])
    target_payload = {**manager._owner_payload(projected), 'retention_original_path': record.worktree_path,
                      'retention_token': data['token']}
    target_claim = _common_owner_claim_path(Path(record.repo_common_dir), Path(data['destination']))
    target = (json.loads(target_claim.read_text(encoding='utf-8-sig'))
              if target_claim.exists() or target_claim.is_symlink() else None)
    target_matches = target == target_payload or (target is None and data['phase'] in {'restored', 'cancelled'})
    allowed = [data['original_marker'], manager._owner_payload(projected), manager._owner_payload(record)]
    if data['phase'] == 'retained':
        allowed = [manager._owner_payload(projected)]
    if data['phase'] in {'prepared', 'cancelled'}:
        allowed = [data['original_marker']]
    if common != data['original_claim'] or not target_matches or marker not in allowed:
        raise RetentionUnknown('owner claims changed')


def _new_receipt(manager, record, source, intent):
    path = Path(record.worktree_path).resolve()
    token = uuid.uuid4().hex
    destination = path.parent / ('.hermes-retained-' + token)
    if destination.exists() or destination.is_symlink():
        raise RetentionUnknown('destination occupied')
    root_identity = _identity(path)
    if path.parent.stat().st_dev != root_identity[0]:
        raise RetentionUnknown('cross-device retention refused')
    intent = reserve_retention(manager._db, record, intent=intent)
    original_claim, original_marker = _claims(manager, record, path)
    if json.loads(intent).get('binding') != asdict(record):
        raise RetentionUnknown('cleanup reservation changed')
    data = {
        'original_claim': original_claim, 'original_marker': original_marker,
        'version': 1, 'token': token, 'binding': _binding(record),
        'database': str(manager._db.db_path.resolve()),
        'database_identity': _file_identity(manager._db.db_path),
        'destination': str(destination), 'root_identity': root_identity,
        'admin': _admin(manager, path), 'admin_identity': _identity(Path(_admin(manager, path))),
        'environment': _environment(manager, record, path), 'original_state': record.state, 'phase': 'prepared',
        'intent': intent,
    }
    _write(receipt_path(record), data, create=True)
    return data


def _location(manager, record, source, data, *, occupied_original=False, occupied_destination=False):
    original, target = Path(record.worktree_path), Path(data['destination'])
    candidates = [(name, path) for name, path in [('original', original), ('retained', target)]
                  if path.exists() or path.is_symlink()]
    if occupied_original and target.exists():
        candidates = [('retained', target)]
    if occupied_destination:
        candidates = [('original', original)]
    if len(candidates) != 1:
        raise RetentionUnknown('physical outcome ambiguous')
    name, path = candidates[0]
    if (_identity(path) != data['root_identity'] or _admin(manager, path) != data['admin']
            or _identity(Path(data['admin'])) != data['admin_identity']
            or manager._listed_worktree(source, path) != 'refs/heads/' + record.branch):
        raise RetentionUnknown('Git registration changed')
    return name, path


def _archive(record, data):
    path = receipt_path(record)
    archived = path.with_name(path.name + '.' + data['token'] + '.' + data['phase'])
    if archived.exists():
        raise RetentionUnknown('terminal receipt already exists')
    os.rename(path, archived)
    _sync_parent(path)


def _project_claim(manager, record, destination, data):
    from agent.conversation_worktree import _common_owner_claim_path
    projected = replace(record, worktree_path=str(destination))
    claim = _common_owner_claim_path(Path(record.repo_common_dir), destination)
    if claim.exists() or claim.is_symlink():
        raise RetentionUnknown('destination claim already exists')
    # The new claim is ownership for preservation/GC, never an execution lease.
    payload = {**manager._owner_payload(projected), 'retention_original_path': record.worktree_path,
               'retention_token': data['token']}
    _write_claim(claim, payload)
    return projected


def _retire_projected_claim(manager, record, data):
    from agent.conversation_worktree import _common_owner_claim_path
    destination = Path(data['destination'])
    claim = _common_owner_claim_path(Path(record.repo_common_dir), destination)
    expected = {**manager._owner_payload(replace(record, worktree_path=str(destination))),
                'retention_original_path': record.worktree_path, 'retention_token': data['token']}
    if not claim.exists() and not claim.is_symlink() and data['phase'] in {'restored', 'cancelled'}:
        _sync_parent(claim)
        return
    info = claim.lstat()
    if not stat.S_ISREG(info.st_mode) or json.loads(claim.read_text(encoding='utf-8-sig')) != expected:
        raise RetentionUnknown('destination claim changed')
    claim.unlink()
    _sync_parent(claim)


def _terminal_transition(manager, record, data, *, retain_intent=False):
    from hermes_state_worktree_cleanup import INTENT_PREFIX
    saved = manager._db._read_one('SELECT value FROM state_meta WHERE key=?',
                                 (INTENT_PREFIX + record.root_session_id,))
    if saved is None:
        current = manager._db.get_conversation_worktree(record.root_session_id)
        if (data['phase'] not in {'restored', 'cancelled'} or current is None
                or _binding(current) != data['binding'] or current.state != data['original_state']):
            raise RetentionUnknown('terminal commit cannot be proved')
        return
    if saved[0] != data['intent']:
        raise RetentionUnknown('terminal intent changed')
    manager._db.transition_conversation_retention(record, state=data['original_state'],
                                                  intent=data['intent'], retain_intent=retain_intent)


def _complete_terminal(manager, record, data):
    # Keep the terminal receipt fencing native leases through exact intent release.
    _retire_projected_claim(manager, record, data)
    _terminal_transition(manager, record, data)
    _archive(record, data)
    outcome = 'restored' if data['phase'] == 'restored' else 'refused'
    return replace(_result(outcome, data, reason=data.get('cancellation_reason')),
                   released_cleanup_intent=data['intent'])


def _cancel(manager, record, source, data, reason, *, occupied_destination=False):
    location, path = _location(manager, record, source, data, occupied_destination=occupied_destination)
    if location != 'original':
        raise RetentionUnknown('cancellation location changed')
    _verify_claims(manager, record, path, data)
    if _environment(manager, record, path) != data['environment']:
        raise RetentionUnknown('environment identity changed')
    manager._ensure_git_worktree_locked(source, record)
    # Only a synchronous proved refusal enters this recoverable terminal phase.
    cancelled = {**data, 'phase': 'cancelled', 'cancellation_reason': reason}
    _write(receipt_path(record), cancelled)
    _terminal_transition(manager, record, cancelled, retain_intent=True)
    return _complete_terminal(manager, record, cancelled)


def _write_claim(path, data):
    # Existing common claims need not be private, but every new claim is0600.
    if path.is_symlink() or path.exists():
        raise RetentionUnknown('destination claim occupied')
    _write_new(path, data)
    _sync_parent(path)


def _result(outcome, data=None, *, reason=None):
    from agent.conversation_worktree import CleanupResult, CleanupVerdict
    return CleanupResult(
        False, CleanupVerdict(outcome in {'retained', 'restored'}, (reason or 'unknown',) if reason or outcome == 'unknown' else ()),
        failure_phase='retention' if outcome == 'unknown' else None,
        failure_message='Retention requires explicit operator reconciliation.' if outcome == 'unknown' else None,
        outcome=outcome, retained_path=data['destination'] if data and outcome != 'restored' else None,
        recovery_required=outcome == 'unknown',
    )


def _finish(manager, record, source, data, location):
    path = Path(record.worktree_path) if location == 'original' else Path(data['destination'])
    projected = replace(record, worktree_path=str(path))
    _verify_claims(manager, record, path, data)
    if _environment(manager, projected, path) != data['environment']:
        raise RetentionUnknown('environment identity changed')
    manager._ensure_git_worktree_locked(source, projected)
    manager._ensure_owner_marker(projected)
    state = data['original_state'] if location == 'original' else 'retained'
    if location == 'original':
        _terminal_transition(manager, record, data, retain_intent=True)
    else:
        manager._db.transition_conversation_retention(record, state=state, intent=data['intent'])
    data = {**data, 'phase': 'restored' if location == 'original' else 'retained'}
    _write(receipt_path(record), data)
    if location == 'original':
        return _complete_terminal(manager, record, data)
    return _result('restored' if location == 'original' else 'retained', data)


def _move(manager, record, source, data, *, restoring=False):
    original, destination = Path(record.worktree_path), Path(data['destination'])
    start, finish = (destination, original) if restoring else (original, destination)
    if finish.exists() or finish.is_symlink():
        if not restoring:
            return _cancel(manager, record, source, data, 'destination occupied', occupied_destination=True)
        return _result('refused', data, reason='destination occupied')
    if restoring:
        if _environment(manager, replace(record, worktree_path=str(start)), start) != data['environment']:
            return _result('refused', data, reason='environment identity changed')
        data = {**data, 'phase': 'restoring'}
        _write(receipt_path(record), data)
    unlocked = manager._run_git(source, ['worktree', 'unlock', str(start)], manager._policy.create_timeout, 'retention')
    if unlocked.returncode:
        raise RetentionUnknown('Git unlock refused')
    moved = manager._run_git(source, ['worktree', 'move', str(start), str(finish)], manager._policy.create_timeout, 'retention')
    location, _ = _location(manager, record, source, data)
    expected = 'original' if restoring else 'retained'
    if location != expected:
        if moved.returncode:
            manager._ensure_git_worktree_locked(source, replace(record, worktree_path=str(start)))
            if not restoring:
                return _cancel(manager, record, source, data, 'Git move refused')
            retained = {**data, 'phase': 'retained'}
            _verify_claims(manager, record, start, retained)
            if _environment(manager, replace(record, worktree_path=str(start)), start) != data['environment']:
                raise RetentionUnknown('environment identity changed')
            _write(receipt_path(record), retained)
            return _result('refused', data, reason='Git move refused')
        raise RetentionUnknown('Git move outcome ambiguous')
    data = {**data, 'phase': 'restore_moved' if restoring else 'moved'}
    _write(receipt_path(record), data)
    return _finish(manager, record, source, data, location)


def _terminal_receipt(manager, record, source):
    path = receipt_path(record)
    candidates = [item for phase in ('restored', 'cancelled')
                  for item in path.parent.glob(path.name + '.*.' + phase)]
    if not candidates:
        return _result('refused', reason='no retention receipt')
    latest = max(candidates, key=lambda item: item.stat().st_mtime_ns)
    data = _read(record, manager._db, latest)
    location, physical = _location(manager, record, source, data,
                                   occupied_destination=data['phase'] == 'cancelled')
    if record.state != data['original_state'] or location != 'original':
        raise RetentionUnknown('terminal state changed')
    _verify_claims(manager, record, physical, data)
    if _environment(manager, record, physical) != data['environment']:
        raise RetentionUnknown('environment identity changed')
    _terminal_transition(manager, record, data)
    outcome = 'restored' if data['phase'] == 'restored' else 'refused'
    return replace(_result(outcome, data, reason=data.get('cancellation_reason')),
                   released_cleanup_intent=data['intent'])


def _apply(manager, record, source, action, active, liveness, intent):
    path = receipt_path(record)
    data = _read(record, manager._db) if path.exists() or path.is_symlink() else None
    if active or liveness != 'inactive':
        return _result('refused', data, reason='active' if active or liveness == 'active' else 'unknown')
    if data is None:
        if action != 'quarantine':
            if action in {'restore', 'reconcile'}:
                return _terminal_receipt(manager, record, source)
            return _result('refused', reason='no retention receipt')
        verdict = manager._inspect_cleanup_record(record, active_session_bound=False, root_liveness=liveness)
        if not verdict.allowed:
            return _result('refused', reason=verdict.reasons[0])
        data = _new_receipt(manager, record, source, intent)
        _project_claim(manager, record, Path(data['destination']), data)
        return _move(manager, record, source, data)
    location, _ = _location(manager, record, source, data,
                            occupied_original=action == 'restore' and data['phase'] == 'retained',
                            occupied_destination=data['phase'] == 'cancelled')
    if action == 'restore' and location == 'retained' and (Path(record.worktree_path).exists() or Path(record.worktree_path).is_symlink()):
        return _result('refused', data, reason='original path occupied')
    _verify_claims(manager, record, Path(data['destination']) if location == 'retained' else Path(record.worktree_path), data)
    if action == 'inspect':
        if location == 'retained' and data['phase'] == 'retained' and record.state == 'retained':
            return _result('retained', data)
        return _result('unknown', data)
    if action == 'reconcile':
        if location == 'original' and data['phase'] == 'cancelled':
            return _cancel(manager, record, source, data, data['cancellation_reason'], occupied_destination=True)
        if location == 'retained' and data['phase'] in {'moved', 'retained'}:
            return _finish(manager, record, source, data, location)
        if location == 'original' and data['phase'] in {'restore_moved', 'restored'}:
            return _finish(manager, record, source, data, location)
        raise RetentionUnknown('reconciliation cannot prove completion')
    if location == 'retained' and data['phase'] == 'retained' and record.state == 'retained':
        return _move(manager, record, source, data, restoring=True) if action == 'restore' else _result('retained', data)
    raise RetentionUnknown('unfinished retention operation')


def operate(manager, root, *, action, active=False, intent=None):
    record = manager._db.get_conversation_worktree(root)
    data = None
    if record is None:
        return _result('refused', reason='unknown')
    try:
        common = Path(record.repo_common_dir).resolve()
        source = manager._durable_source(record)
        with manager._root_lock(common, root), manager._repository_lock(common):
            record = manager._db.get_conversation_worktree(root)
            if record is None:
                return _result('unknown')
            with manager._root_lease_liveness(record) as liveness:
                return _apply(manager, record, source, action, active, liveness, intent)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error):
        # No compensating move or receipt erasure after an uncertain operation.
        try:
            data = _read(record, manager._db)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            data = None
        return _result('unknown', data)
