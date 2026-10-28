"""Kernel-bound router retirement, without any live kernel/process/provider access."""
import hashlib
import importlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_cli.local_runtime import binaries, bootstrap, tree_custody

pytestmark = pytest.mark.platforms('macos')

HOST = '11111111-1111-4111-8111-111111111111'
BOOT1 = '22222222-2222-4222-8222-222222222222'
BOOT2 = '33333333-3333-4333-8333-333333333333'
VOLUME = '44444444-4444-4444-8444-444444444444'


@pytest.fixture
def case(tmp_path, monkeypatch):
    name = 'hermes_cli.local_runtime.reconciliation'
    api = importlib.import_module(name)
    home = tmp_path / 'home'
    root = home / 'runtimes' / 'llamacpp'
    root.mkdir(parents=True)
    for key in ('HOME', 'USERPROFILE'):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    for module in (api, binaries, bootstrap):
        monkeypatch.setattr(module, 'runtimes_root', lambda: root)
    identity = dict(host=HOST, boot=BOOT1)
    monkeypatch.setattr(api, '_native_identity', lambda: dict(identity))
    monkeypatch.setattr(api, '_volume_identity', lambda fd: VOLUME)
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    state = dict(pid=os.getpid(), create_time=100.0, start_time=100.0,
                 executable='/synthetic/llama-server', owner_pid=os.getpid(),
                 owner_create_time=90.0, owner_start_time=90.0,
                 base_url='http://127.0.0.1:18434/v1', api_key='PRIVATE_SENTINEL')
    path = root / 'server.json'
    raw = (json.dumps(state) + '\n').encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    digest = hashlib.sha256(raw).hexdigest()
    return api, root, path, raw, digest, identity


def checkpoint(case):
    api, _, _, _, digest, _ = case
    return api.checkpoint(expected_sha256=digest, timeout_s=0)


def test_checkpoint_is_not_same_boot_retirement(case):
    api, root, path, raw, digest, _ = case
    proof = checkpoint(case)
    with pytest.raises(RuntimeError, match='kernel_boundary_required'):
        api.reconcile(expected_sha256=digest, checkpoint_id=proof['checkpoint_id'], timeout_s=0)
    assert path.read_bytes() == raw
    assert not list(root.glob('.router-retirement-*.json'))


def test_restart_proof_preserves_original_and_durable_private_copy(case):
    api, _, path, raw, digest, identity = case
    proof = checkpoint(case)
    identity['boot'] = BOOT2
    result = api.reconcile(expected_sha256=digest, checkpoint_id=proof['checkpoint_id'], timeout_s=0)
    assert result['outcome'] == 'retired_in_place'
    assert path.read_bytes() == raw
    backup = Path(result['recovery_path'])
    assert backup.read_bytes() == raw
    assert backup.stat().st_mode & 0o777 == 0o600
    assert backup.parent.stat().st_mode & 0o777 == 0o700
    assert 'PRIVATE_SENTINEL' not in json.dumps(result)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        assert api.retirement_matches(path)
        assert bootstrap._incumbent_allows_boot()


def test_same_boot_live_record_never_becomes_replaceable(case):
    api, _, path, raw, _, _ = case
    checkpoint(case)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        assert not api.retirement_matches(path)
    assert path.read_bytes() == raw


def test_pid_reuse_does_not_kill_or_adopt_current_process(case):
    api, _, path, raw, digest, identity = case
    proof = checkpoint(case)
    identity['boot'] = BOOT2
    api.reconcile(expected_sha256=digest, checkpoint_id=proof['checkpoint_id'], timeout_s=0)
    # The saved PID deliberately equals this live test process; prior-kernel closure
    # applies only to its old incarnation, never this process or its children.
    intent = None
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        intent = tree_custody.reserve_uncontained_tree(path)
    assert intent['tree_custody'] == 'unverified'
    assert intent['owner_pid'] == os.getpid()
    assert json.loads(path.read_text()) == intent
    assert path.read_bytes() != raw
    assert not api.retirement_matches(path)


def test_retired_replacement_requires_actual_boot_lock(case):
    api, _, path, raw, digest, identity = case
    proof = checkpoint(case)
    identity['boot'] = BOOT2
    api.reconcile(expected_sha256=digest, checkpoint_id=proof['checkpoint_id'], timeout_s=0)
    with pytest.raises(RuntimeError, match='runtime_lock_required'):
        tree_custody.reserve_uncontained_tree(path)
    assert path.read_bytes() == raw


def test_changed_record_after_checkpoint_refuses(case):
    api, _, path, raw, digest, identity = case
    proof = checkpoint(case)
    identity['boot'] = BOOT2
    path.write_bytes(raw + b' ')
    with pytest.raises(RuntimeError, match='record_changed'):
        api.reconcile(expected_sha256=digest, checkpoint_id=proof['checkpoint_id'], timeout_s=0)
    assert path.read_bytes() == raw + b' '


def test_changed_host_refuses(case):
    api, _, path, raw, digest, identity = case
    proof = checkpoint(case)
    identity.update(host=BOOT2, boot=BOOT2)
    with pytest.raises(RuntimeError, match='host_changed'):
        api.reconcile(expected_sha256=digest, checkpoint_id=proof['checkpoint_id'], timeout_s=0)
    assert path.read_bytes() == raw


def test_unknown_native_identity_refuses(case, monkeypatch):
    api, _, path, raw, digest, _ = case
    def unknown():
        raise OSError('synthetic unavailable')
    monkeypatch.setattr(api, '_native_identity', unknown)
    with pytest.raises(RuntimeError, match='identity_unavailable'):
        api.checkpoint(expected_sha256=digest, timeout_s=0)
    assert path.read_bytes() == raw


def test_missing_checkpoint_is_not_reconstructed_after_restart(case):
    api, _, path, raw, digest, identity = case
    identity['boot'] = BOOT2
    with pytest.raises(RuntimeError, match='checkpoint_unavailable'):
        api.reconcile(expected_sha256=digest, checkpoint_id='a' * 32, timeout_s=0)
    assert path.read_bytes() == raw


def test_boot_lock_contention_refuses_without_checkpoint(case):
    api, root, path, raw, digest, _ = case
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(api.checkpoint, expected_sha256=digest, timeout_s=0)
            with pytest.raises(RuntimeError, match='runtime_lock_busy'):
                future.result(timeout=5)
    assert path.read_bytes() == raw
    assert not list(root.glob('.router-checkpoint-*.json'))


def test_second_incarnation_gets_independent_proof_without_erasing_prior(case):
    api, _, path, raw, digest, identity = case
    first = checkpoint(case)
    identity['boot'] = BOOT2
    retired = api.reconcile(expected_sha256=digest, checkpoint_id=first['checkpoint_id'], timeout_s=0)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        tree_custody.reserve_uncontained_tree(path)
    second_state = json.loads(raw)
    second_state.update(create_time=200.0, start_time=200.0, owner_create_time=190.0,
                        owner_start_time=190.0, api_key='SECOND_PRIVATE_SENTINEL')
    second_raw = json.dumps(second_state).encode()
    path.write_bytes(second_raw)
    second_digest = hashlib.sha256(second_raw).hexdigest()
    second = api.checkpoint(expected_sha256=second_digest, timeout_s=0)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        assert not api.retirement_matches(path)
    identity['boot'] = '55555555-5555-4555-8555-555555555555'
    again = api.reconcile(expected_sha256=second_digest, checkpoint_id=second['checkpoint_id'], timeout_s=0)
    assert again['checkpoint_id'] != retired['checkpoint_id']
    assert Path(again['receipt_path']).exists()
    assert Path(retired['receipt_path']).exists()
    assert Path(retired['recovery_path']).read_bytes() == raw
    assert path.read_bytes() == second_raw


@pytest.mark.parametrize('stage', ['backup', 'receipt'])
def test_retirement_publication_failure_never_changes_original(case, monkeypatch, stage):
    api, root, path, raw, digest, identity = case
    proof = checkpoint(case)
    identity['boot'] = BOOT2
    write = api.evidence.exclusive_write
    def fault(target, data):
        if (stage == 'backup' and target.name == 'server.json' or
                stage == 'receipt' and target.name.startswith('.router-retirement-')):
            raise OSError('synthetic publication failure')
        return write(target, data)
    monkeypatch.setattr(api.evidence, 'exclusive_write', fault)
    with pytest.raises(OSError, match='publication failure'):
        api.reconcile(expected_sha256=digest, checkpoint_id=proof['checkpoint_id'], timeout_s=0)
    assert path.read_bytes() == raw
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        assert not api.retirement_matches(path)
    if stage == 'receipt':
        assert (root / ('.router-recovery-' + proof['checkpoint_id']) / 'server.json').read_bytes() == raw


@pytest.mark.parametrize('after_replace', [False, True])
def test_atomic_replacement_failure_keeps_a_present_admission_fence(case, monkeypatch, after_replace):
    import utils
    api, _, path, raw, digest, identity = case
    proof = checkpoint(case)
    identity['boot'] = BOOT2
    result = api.reconcile(expected_sha256=digest, checkpoint_id=proof['checkpoint_id'], timeout_s=0)
    write = utils.atomic_json_write
    def fault(*args, **kwargs):
        if after_replace:
            write(*args, **kwargs)
        raise OSError('synthetic atomic failure')
    monkeypatch.setattr(utils, 'atomic_json_write', fault)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with pytest.raises(OSError, match='atomic failure'):
            tree_custody.reserve_uncontained_tree(path)
        assert path.exists()
        if after_replace:
            assert json.loads(path.read_text())['tree_custody'] == 'unverified'
            assert not api.retirement_matches(path)
        else:
            assert path.read_bytes() == raw
    assert Path(result['recovery_path']).read_bytes() == raw


def test_publicly_readable_recovery_directory_refuses_admission(case):
    api, _, path, raw, digest, identity = case
    proof = checkpoint(case)
    identity['boot'] = BOOT2
    result = api.reconcile(expected_sha256=digest, checkpoint_id=proof['checkpoint_id'], timeout_s=0)
    Path(result['recovery_path']).parent.chmod(0o755)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        assert not api.retirement_matches(path)
    assert path.read_bytes() == raw


def test_explicit_maintenance_cli_never_emits_private_record_or_starts_server(case, capsys):
    api, _, path, raw, digest, identity = case
    api.main(['checkpoint', '--expected-sha256', digest])
    output = capsys.readouterr().out
    first = json.loads(output)
    assert first['outcome'] == 'checkpointed_only'
    assert 'PRIVATE_SENTINEL' not in output
    identity['boot'] = BOOT2
    api.main(['reconcile', '--expected-sha256', digest, '--checkpoint-id', first['checkpoint_id']])
    output = capsys.readouterr().out
    assert json.loads(output)['server_started'] is False
    assert 'PRIVATE_SENTINEL' not in output
    assert path.read_bytes() == raw


def test_unavailable_native_file_identity_refuses_before_platform_open_flags(case, monkeypatch):
    api, _, path, _, _, _ = case
    def unavailable():
        raise RuntimeError('native_file_identity_unavailable')
    with monkeypatch.context() as scoped:
        scoped.setattr(api.evidence, '_uid', unavailable)
        scoped.setattr(api.evidence.os, 'open', lambda *a, **k: pytest.fail('opened before native identity guard'))
        with pytest.raises(RuntimeError, match='native_file_identity_unavailable'):
            api.evidence.private_read(path)


@pytest.mark.parametrize('kind', ['symlink', 'malformed', 'incomplete'])
def test_uncertain_record_refuses_checkpoint(case, kind):
    api, root, path, raw, _, _ = case
    if kind == 'symlink':
        alternate = root / 'original.json'
        path.rename(alternate)
        path.symlink_to(alternate)
    else:
        path.write_bytes(b'{' if kind == 'malformed' else b'{"pid":17}')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError):
        api.checkpoint(expected_sha256=digest, timeout_s=0)
    assert path.read_bytes() == (raw if kind == 'symlink' else b'{' if kind == 'malformed' else b'{"pid":17}')
