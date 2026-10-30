"""Operator-owned targetless startup hold with crash-safe prior-state restoration."""
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path
import hashlib
import json
import uuid

from hermes_cli import startup_hold
from hermes_state_runtime import RuntimeStoreError


def _bound_id(nonce, previous):
    digest = hashlib.sha256(json.dumps([nonce, previous], ensure_ascii=True).encode()).hexdigest()[:32]
    return nonce + digest


def _journal():
    journal = startup_hold.read_journal()
    if (not isinstance(journal, dict) or set(journal) != {'schema', 'phase', 'marker'}
            or journal['schema'] != 1 or journal['phase'] not in {'preparing', 'held', 'restoring'}
            or not isinstance(journal['marker'], dict)):
        raise RuntimeStoreError('startup_hold_unavailable')
    body = journal['marker']
    value = body.get(startup_hold.REASON)
    if (not isinstance(value, dict) or set(value) != {'hold_id', 'previous_drain'}
            or not isinstance(value['hold_id'], str) or len(value['hold_id']) != 64
            or any(c not in '0123456789abcdef' for c in value['hold_id'])
            or (value['previous_drain'] is not None and not isinstance(value['previous_drain'], str))
            or 'kanban_once' in body or body.get('action') != 'drain'
            or body.get('principal') != 'startup-hold'
            or value['hold_id'] != _bound_id(value['hold_id'][:32], value['previous_drain'])):
        raise RuntimeStoreError('startup_hold_unavailable')
    return journal


def state():
    return _journal()['marker'][startup_hold.REASON]


def status():
    return dict(held=True, mode=startup_hold.REASON, hold_id=state()['hold_id'], armed=False)


def _refuse_legacy_writers(covered=(), retired=frozenset()):
    from hermes_cli.startup_hold_writers import refuse_legacy_writers
    refuse_legacy_writers(covered=covered, retired=retired)


def _checkout_root():
    return Path(__file__).resolve().parents[1]


@contextmanager
def _checkout_custody(*, stable_identity=False):
    from hermes_cli import update_lock
    from hermes_cli.runtime_state import runtime_lock
    root = _checkout_root()
    if update_lock._HELD is not None:
        raise RuntimeStoreError('startup_hold_checkout_busy')
    holder = update_lock._acquire_checkout(root)
    if holder is not None:
        raise RuntimeStoreError('startup_hold_checkout_busy')
    try:
        proof = update_lock._HELD
        if not proof or not proof.get('owned') or proof.get('fd') is None:
            raise RuntimeStoreError('startup_hold_checkout_busy')
        options = {'stable_identity': True} if stable_identity else {}
        with runtime_lock(root, timeout=0, **options) as held:
            if not held:
                raise RuntimeStoreError('startup_hold_checkout_busy')
            if startup_hold.recovery_pending(root):
                raise RuntimeStoreError('startup_hold_recovery_pending')
            yield
    finally:
        update_lock._release_checkout()


@contextmanager
def _transaction():
    from agent.estop import is_engaged
    from hermes_cli.process_ledger_lock import ledger_gate
    from hermes_cli.startup_hold_reboot import reboot_exclusion
    from gateway.drain_kanban import _marker_lock, _quiescent_profiles
    from hermes_cli.kanban_db_connect import _dispatch_host_admission_lock
    if not is_engaged():
        raise RuntimeStoreError('startup_hold_requires_pause')
    with _checkout_custody(), _dispatch_host_admission_lock(strict=True) as acquired:
        if not acquired:
            raise RuntimeStoreError('startup_hold_dispatch_busy')
        with startup_hold.admission_gate(), startup_hold.writer_exclusion() as covered, _marker_lock(), _quiescent_profiles(), ledger_gate(), reboot_exclusion() as proof:
            if proof.record is None:
                _refuse_legacy_writers(covered)
            else:
                _refuse_legacy_writers(covered, proof.rows)
            proof.check()
            if not is_engaged():
                raise RuntimeStoreError('startup_hold_requires_pause')
            yield


def _publish_journal(journal, phase):
    from utils import atomic_json_write
    journal['phase'] = phase
    atomic_json_write(startup_hold.journal_path(), journal, mode=0o600, fsync_dir=True)


def _validate_publication(marker, journal):
    for value, limit in ((marker, 1_048_576), (journal, 4_194_304)):
        if len(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')) + 1 > limit:
            raise RuntimeStoreError('startup_hold_state_too_large')


def begin():
    from gateway.drain_kanban import held
    from utils import atomic_json_write
    with _transaction():
        if held():
            raise RuntimeStoreError('startup_hold_exists')
        raw = startup_hold.read_marker_bytes()
        previous = raw.decode('utf-8') if raw is not None else None
        value = {'hold_id': _bound_id(uuid.uuid4().hex, previous), 'previous_drain': previous}
        marker = {'action': 'drain', 'principal': 'startup-hold',
                  'requested_at': datetime.now(UTC).isoformat(), startup_hold.REASON: value}
        journal = {'schema': 1, 'phase': 'preparing', 'marker': marker}
        _validate_publication(marker, journal)
        _publish_journal(journal, 'preparing')
        atomic_json_write(startup_hold.marker_path(), marker, mode=0o600, fsync_dir=True)
        _publish_journal(journal, 'held')
    return value


def restore(*, hold_id):
    from utils import atomic_write_bytes, fsync_directory
    with _transaction():
        journal = _journal()
        value = journal['marker'][startup_hold.REASON]
        if value['hold_id'] != hold_id:
            raise RuntimeStoreError('startup_hold_unavailable')
        previous = value['previous_drain']
        prior = previous.encode('utf-8') if previous is not None else None
        current = startup_hold.read_marker_bytes()
        matches = startup_hold.read_marker() == journal['marker']
        recoverable = journal['phase'] in {'preparing', 'restoring'} and current == prior
        if current is not None and not matches and not recoverable:
            raise RuntimeStoreError('startup_hold_unavailable')
        _publish_journal(journal, 'restoring')
        if prior is None:
            startup_hold.marker_path().unlink(missing_ok=True)
            fsync_directory(startup_hold.marker_path().parent)
        else:
            atomic_write_bytes(startup_hold.marker_path(), prior, mode=0o600, fsync_dir=True)
        startup_hold.journal_path().unlink()
        fsync_directory(startup_hold.journal_path().parent)


def native_config(config, *, replace=False, force=False):
    """Held boot owns only its native listener; forced replacement is never available."""
    if not startup_hold.active():
        return config
    if replace or force:
        raise RuntimeError('startup_hold: replacement is unavailable')
    startup_hold.latch_boot()
    from gateway.config import GatewayConfig
    return GatewayConfig(platforms={}, multiplex_profiles=False)


def describe(authority):
    return {'instance_id': authority.instance_id, 'profile_id': authority.profile_id,
            'authority_epoch': authority.epoch, 'capabilities': ['startup-hold-v1'],
            'session_create': {'sources': [], 'parameters': []}, 'skin': None}
