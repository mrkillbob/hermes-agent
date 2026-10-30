"""Explicit prospective kernel-bound retirement; never start, signal or infer closure.

The original server.json stays untouched. A separately authorized normal start may
replace that exact retired record only after a durable private copy exists.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import uuid

from hermes_cli.local_runtime.binaries import runtimes_root
from hermes_cli.local_runtime import reconciliation_io as evidence

_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _checkpoint_path(digest):
    return runtimes_root() / ('.router-checkpoint-' + digest + '.json')


def _retirement_path(digest):
    return runtimes_root() / ('.router-retirement-' + digest + '.json')


def _native_identity():
    from hermes_platform.host.boot_identity import local_boot_identity
    return local_boot_identity()


def _volume_identity(fd):
    from hermes_platform.host.volume_identity import local_volume_identity
    return local_volume_identity(fd)


def _identity():
    try:
        return evidence.boot_identity(_native_identity())
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError('identity_unavailable') from exc


@contextmanager
def _locked(timeout_s):
    from hermes_cli.local_runtime import bootstrap
    with bootstrap._TRANSITION_LOCK, bootstrap._cross_process_boot_lock(timeout_s=timeout_s) as acquired:
        if not acquired:
            raise RuntimeError('runtime_lock_busy')
        if bootstrap._SUPERVISOR is not None:
            raise RuntimeError('owned_runtime_present')
        yield


def _state(expected_sha256):
    from hermes_cli.local_runtime.recovery import _valid_birth, _valid_pid
    if not isinstance(expected_sha256, str) or not _DIGEST.fullmatch(expected_sha256):
        raise RuntimeError('expected_state_digest_required')
    try:
        raw, binding = evidence.capture(runtimes_root(), _volume_identity)
        value = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise RuntimeError('record_unqualified') from exc
    if binding['sha256'] != expected_sha256:
        raise RuntimeError('record_changed')
    if (not isinstance(value, dict) or any(not _valid_pid(value.get(k)) for k in ('pid', 'owner_pid')) or
            any(not _valid_birth(value.get(k)) for k in ('create_time', 'owner_create_time')) or
            value['owner_create_time'] > value['create_time'] or
            not isinstance(value.get('executable'), str) or not value['executable']):
        raise RuntimeError('modern_record_required')
    return raw, binding


def checkpoint(*, expected_sha256, timeout_s=0):
    """Capture current facts; this grants no same-boot replacement or run authority."""
    with _locked(timeout_s):
        identity = _identity()
        _, binding = _state(expected_sha256)
        identifier = uuid.uuid4().hex
        value = dict(schema=1, checkpoint_id=identifier, identity=identity,
                     binding=binding, source=evidence.source_identity())
        if _identity() != identity or _state(expected_sha256)[1] != binding:
            raise RuntimeError('checkpoint_facts_changed')
        evidence.exclusive_write(_checkpoint_path(expected_sha256), evidence.encoded(value))
        return dict(outcome='checkpointed_only', checkpoint_id=identifier, state_sha256=expected_sha256,
                    same_boot_admission=False, original_preserved=True)


def _proof(expected_sha256, checkpoint_id):
    if not isinstance(checkpoint_id, str) or not re.fullmatch(r'[0-9a-f]{32}', checkpoint_id):
        raise RuntimeError('checkpoint_id_required')
    try:
        checkpoint_raw, saved = evidence.read_json(_checkpoint_path(expected_sha256))
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeError('checkpoint_unavailable') from exc
    if (saved.get('schema') != 1 or saved.get('checkpoint_id') != checkpoint_id or
            saved.get('source') != evidence.source_identity()):
        raise RuntimeError('checkpoint_changed')
    prior = evidence.boot_identity(saved.get('identity'))
    current = _identity()
    if prior['host'] != current['host']:
        raise RuntimeError('host_changed')
    if prior['boot'] == current['boot']:
        raise RuntimeError('kernel_boundary_required')
    raw, binding = _state(expected_sha256)
    if binding != saved.get('binding'):
        raise RuntimeError('record_changed')
    return raw, saved, current, hashlib.sha256(checkpoint_raw).hexdigest()


def reconcile(*, expected_sha256, checkpoint_id, timeout_s=0):
    """Retire only the checkpointed prior-kernel incarnation, preserving raw bytes."""
    with _locked(timeout_s):
        raw, saved, current, checkpoint_sha = _proof(expected_sha256, checkpoint_id)
        root = runtimes_root()
        receipt_path = _retirement_path(expected_sha256)
        if receipt_path.exists() or receipt_path.is_symlink():
            raise RuntimeError('retirement_already_exists')
        recovery = root / ('.router-recovery-' + checkpoint_id)
        recovery.mkdir(mode=0o700)
        backup = recovery / 'server.json'
        evidence.exclusive_write(backup, raw)
        again = _proof(expected_sha256, checkpoint_id)
        if again != (raw, saved, current, checkpoint_sha):
            raise RuntimeError('retirement_facts_changed')
        value = dict(schema=1, checkpoint_id=checkpoint_id, checkpoint_sha256=checkpoint_sha,
                     original_binding=saved['binding'], source=saved['source'],
                     prior_identity=saved['identity'], retired_identity=current,
                     recovery_path=str(backup))
        evidence.exclusive_write(receipt_path, evidence.encoded(value))
        return dict(outcome='retired_in_place', checkpoint_id=checkpoint_id,
                    state_sha256=expected_sha256, recovery_path=str(backup), receipt_path=str(receipt_path), original_preserved=True,
                    server_started=False)


def _validated_retirement(path):
    from hermes_cli.local_runtime.runtime_lock import require_owned
    require_owned(path)
    if path != runtimes_root() / 'server.json':
        raise RuntimeError('canonical_state_path_required')
    current_raw = evidence.private_read(path)[0]
    _, receipt = evidence.read_json(_retirement_path(hashlib.sha256(current_raw).hexdigest()))
    binding = receipt['original_binding']
    raw, saved, _, checkpoint_sha = _proof(binding['sha256'], receipt['checkpoint_id'])
    if (receipt.get('schema') != 1 or receipt.get('checkpoint_sha256') != checkpoint_sha or
            receipt.get('source') != saved['source'] or binding != saved['binding'] or
            evidence.boot_identity(receipt['prior_identity']) != evidence.boot_identity(saved['identity'])):
        raise RuntimeError('retirement_changed')
    retired = evidence.boot_identity(receipt['retired_identity'])
    prior = evidence.boot_identity(saved['identity'])
    if retired['host'] != prior['host'] or retired['boot'] == prior['boot']:
        raise RuntimeError('retirement_boundary_unqualified')
    recovery = path.parent / ('.router-recovery-' + receipt['checkpoint_id']) / 'server.json'
    if receipt.get('recovery_path') != str(recovery) or recovery.parent.is_symlink():
        raise RuntimeError('recovery_path_changed')
    directory = evidence.private_directory(recovery.parent)
    if evidence.private_read(recovery)[0] != raw:
        raise RuntimeError('recovery_bytes_changed')
    if evidence.private_directory(recovery.parent) != directory:
        raise RuntimeError('recovery_directory_changed')
    return binding


def retirement_matches(path):
    """Called inside the ordinary boot lock; uncertainty never admits replacement."""
    try:
        _validated_retirement(path)
        return True
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return False


def replace_retired_record(path, intent):
    """Normal separately authorized spawn only: never expose an absent-state gap."""
    from hermes_cli.local_runtime.runtime_lock import require_owned
    from utils import atomic_json_write
    digest = hashlib.sha256(evidence.private_read(path)[0]).hexdigest()
    if not _retirement_path(digest).exists():
        raise RuntimeError('local runtime tree custody is unverified; replacement refused')
    require_owned(path)
    binding = _validated_retirement(path)
    if _state(binding['sha256'])[1] != binding:
        raise RuntimeError('record_changed')
    atomic_json_write(path, intent, mode=0o600, fsync_dir=True)


def main(argv=None):
    """Explicit maintenance CLI; no automatic recovery or service boot."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('checkpoint', 'reconcile'))
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--checkpoint-id')
    args = parser.parse_args(argv)
    if args.operation == 'checkpoint':
        result = checkpoint(expected_sha256=args.expected_sha256)
    else:
        result = reconcile(expected_sha256=args.expected_sha256, checkpoint_id=args.checkpoint_id)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
