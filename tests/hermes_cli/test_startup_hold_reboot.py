"""Prospective reboot extinction, without historical receipts or ledger mutations."""
import hashlib
import json
import os

import pytest

pytestmark = pytest.mark.platforms('posix')

_BEFORE = dict(host='11111111-1111-4111-8111-111111111111', boot='22222222-2222-4222-8222-222222222222')
_AFTER = dict(_BEFORE, boot='33333333-3333-4333-8333-333333333333')


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from hermes_cli import dashboard_procs, process_identity, startup_hold_reboot as reboot
    from gateway import drain_startup
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(dashboard_procs, '_iter_process_table', lambda **kwargs: [])
    monkeypatch.setattr(process_identity, '_pid_alive_matches', lambda *args, **kwargs: False)
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: home)
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_BEFORE))
    monkeypatch.setattr(reboot, '_source_binding', lambda: dict(path='source-fixture', sha256='a' * 64))
    (home / 'ESTOP').touch()
    entries = [dict(pid=100 + index, create_time=100.25 + index, purpose='mcp-helper',
                    install='0fcc25fdfa9e', spawner_pid=50, spawner_create=50.25,
                    registered_at=105.25, argv='private fixture') for index in range(3)]
    entries.append(dict(purpose='interactive', argv='unrelated private fixture'))
    path = home / 'spawn-ledger.json'
    path.write_text(json.dumps(entries))
    return home, entries


def prepare(environment):
    from hermes_cli import startup_hold_reboot as reboot
    home, entries = environment
    return reboot.prepare(ledger_sha256=hashlib.sha256((home / 'spawn-ledger.json').read_bytes()).hexdigest(),
                          entry_sha256=[reboot._hash(entry) for entry in entries[:3]],
                          install='0fcc25fdfa9e', spawner=[50, 50.25])


def test_same_boot_cannot_admit_but_new_kernel_can_hold_and_restore(environment, monkeypatch):
    from hermes_cli import startup_hold_reboot as reboot
    from gateway import drain_startup
    home, _ = environment
    ledger = home / 'spawn-ledger.json'
    before = ledger.read_bytes()
    result = prepare(environment)
    assert result['admission_granted'] is False
    assert result['rows'] == 3
    assert 'private fixture' not in json.dumps(result)
    with pytest.raises(RuntimeError, match='startup_hold_reboot_required'):
        drain_startup.begin()
    assert not (home / '.startup-hold.json').exists()
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    value = drain_startup.begin()
    assert drain_startup.status()['armed'] is False
    drain_startup.restore(hold_id=value['hold_id'])
    assert ledger.read_bytes() == before
    assert not (home / '.drain_request.json').exists()
    # An unchanged checkpoint may discharge the same old rows repeatedly.
    next_value = drain_startup.begin()
    drain_startup.restore(hold_id=next_value['hold_id'])
    assert ledger.read_bytes() == before


@pytest.mark.parametrize('field', ['host', 'source', 'root', 'ledger', 'entry', 'install', 'spawner', 'schema'])
def test_mismatched_bindings_refuse(environment, monkeypatch, field):
    from hermes_cli import startup_hold_reboot as reboot
    from gateway import drain_startup
    home, _ = environment
    prepare(environment)
    path = home / '.startup-legacy-reboot.json'
    record = json.loads(path.read_bytes())
    if field in {'source', 'root', 'ledger'}:
        record['binding'][field] = {}
    elif field == 'entry':
        record['entries'] = ['a' * 64]
    elif field == 'schema':
        record['schema'] = True
    else:
        record[field] = 'wrong'
    path.write_text(json.dumps(record))
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    with pytest.raises((RuntimeError, TypeError, ValueError)):
        drain_startup.begin()
    assert not (home / '.startup-hold.json').exists()


def test_byte_identical_ledger_rewrite_invalidates_checkpoint(environment, monkeypatch):
    from hermes_cli import startup_hold_reboot as reboot
    from gateway import drain_startup
    home, _ = environment
    prepare(environment)
    path = home / 'spawn-ledger.json'
    raw = path.read_bytes()
    replacement = home / 'replacement'
    replacement.write_bytes(raw)
    os.replace(replacement, path)
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin()
    assert path.read_bytes() == raw


@pytest.mark.parametrize('alive', [True, None])
def test_live_or_ambiguous_identity_cannot_be_prepared(environment, monkeypatch, alive):
    from hermes_cli import process_identity
    home, _ = environment
    monkeypatch.setattr(process_identity, '_pid_alive_matches', lambda *args, **kwargs: alive)
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        prepare(environment)
    assert not (home / '.startup-legacy-reboot.json').exists()


def test_current_live_identity_still_blocks_after_reboot(environment, monkeypatch):
    from hermes_cli import process_identity, startup_hold_reboot as reboot
    from gateway import drain_startup
    home, _ = environment
    prepare(environment)
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    monkeypatch.setattr(process_identity, '_pid_alive_matches', lambda pid, *args, **kwargs: pid == 100)
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin()
    assert not (home / '.startup-hold.json').exists()


def test_unsettled_writer_debt_is_not_reboot_exempt(environment, monkeypatch):
    from hermes_cli import startup_hold, startup_hold_reboot as reboot
    from gateway import drain_startup
    prepare(environment)
    home, _ = environment
    directory = home / '.startup-writers'
    directory.mkdir(exist_ok=True)
    (directory / 'uncertain.hold').write_text(json.dumps(dict(schema=1, state='unsettled', pid=200)))
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
        drain_startup.begin()
    with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
        prepare(environment)
    assert not startup_hold.journal_present()


def test_unselected_legacy_row_still_blocks(environment, monkeypatch):
    from hermes_cli import startup_hold_reboot as reboot
    from gateway import drain_startup
    home, entries = environment
    reboot.prepare(ledger_sha256=hashlib.sha256((home / 'spawn-ledger.json').read_bytes()).hexdigest(),
                   entry_sha256=[reboot._hash(entries[0])], install='0fcc25fdfa9e', spawner=[50, 50.25])
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        drain_startup.begin()


@pytest.mark.parametrize('damage', [b'', b'{', b'{}', b'[]', b'null', b'\xff'])
def test_torn_or_malformed_checkpoint_is_not_repaired(environment, monkeypatch, damage):
    from hermes_cli import startup_hold_reboot as reboot
    from gateway import drain_startup
    home, _ = environment
    path = home / '.startup-legacy-reboot.json'
    path.write_bytes(damage)
    path.chmod(0o600)
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin()
    with pytest.raises(FileExistsError):
        prepare(environment)
    assert path.read_bytes() == damage


def test_existing_durable_checkpoint_is_not_overwritten_on_retry(environment):
    home, _ = environment
    first = prepare(environment)
    path = home / '.startup-legacy-reboot.json'
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        prepare(environment)
    assert path.read_bytes() == before
    assert json.loads(before)['id'] == first['checkpoint_id']


def test_prepare_respects_pause_and_defers_maintenance(environment):
    from hermes_cli import startup_hold
    home, _ = environment
    assert startup_hold.defer_maintenance(['gateway', 'drain', 'prepare-reboot-reconcile'])
    (home / 'ESTOP').unlink()
    with pytest.raises(RuntimeError, match='startup_hold_requires_pause'):
        prepare(environment)
    assert not (home / '.startup-legacy-reboot.json').exists()


def test_ledger_gate_serializes_normal_registration(environment, monkeypatch):
    from hermes_cli.process_ledger_lock import ledger_gate
    from hermes_cli import process_identity
    home, _ = environment
    before = (home / 'spawn-ledger.json').read_bytes()
    import pm.filesystem
    original = pm.filesystem.lock_fd
    def no_wait(fd, **kwargs):
        return original(fd, wait=False)
    monkeypatch.setattr(pm.filesystem, 'lock_fd', no_wait)
    with ledger_gate():
        assert process_identity.register_child(os.getpid(), 'mcp-helper') is False
    assert (home / 'spawn-ledger.json').read_bytes() == before


@pytest.mark.parametrize('alive', [True, None])
def test_unselected_live_or_unknown_helper_blocks_prepare(environment, monkeypatch, alive):
    from hermes_cli import process_identity
    home, _ = environment
    monkeypatch.setattr(process_identity, '_pid_alive_matches',
                        lambda pid, *args, **kwargs: alive if pid == 102 else False)
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        prepare(environment)
    assert not (home / '.startup-legacy-reboot.json').exists()


def test_real_owner_registration_expires_exemption_without_blocking_restore(environment, monkeypatch):
    from hermes_cli import process_identity, startup_hold_reboot as reboot
    from gateway import drain_startup
    home, _ = environment
    prepare(environment)
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    value = drain_startup.begin()
    assert process_identity.register_self('gateway') is True
    after_registration = (home / 'spawn-ledger.json').read_bytes()
    drain_startup.restore(hold_id=value['hold_id'])
    assert (home / 'spawn-ledger.json').read_bytes() == after_registration
    assert not (home / '.startup-hold.json').exists()


def test_checkpoint_mode_is_private_even_under_restrictive_umask(environment, monkeypatch):
    from hermes_cli import startup_hold_reboot as reboot
    home, _ = environment
    original = reboot._write_checkpoint
    def restrictive(record, check):
        prior = os.umask(0o777)
        try:
            return original(record, check)
        finally:
            os.umask(prior)
    monkeypatch.setattr(reboot, '_write_checkpoint', restrictive)
    prepare(environment)
    assert (home / '.startup-legacy-reboot.json').stat().st_mode & 0o777 == 0o600


def test_interrupted_prepare_leaves_uncommitted_blocking_record(environment, monkeypatch):
    from hermes_cli import startup_hold_reboot as reboot
    from gateway import drain_startup
    home, _ = environment
    original = reboot._write_record
    def interrupted(fd, record):
        original(fd, record)
        raise OSError('interrupted checkpoint fixture')
    monkeypatch.setattr(reboot, '_write_record', interrupted)
    with pytest.raises(OSError):
        prepare(environment)
    assert json.loads((home / '.startup-legacy-reboot.json').read_bytes())['phase'] == 'preparing'
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin()


def test_lost_ack_does_not_make_a_committed_checkpoint_uncommitted(environment, monkeypatch):
    from hermes_cli import startup_hold_reboot as reboot
    from gateway import drain_startup
    home, _ = environment
    original = reboot._write_checkpoint
    def lost_ack(record, check):
        original(record, check)
        raise OSError('lost acknowledgement fixture')
    monkeypatch.setattr(reboot, '_write_checkpoint', lost_ack)
    with pytest.raises(OSError):
        prepare(environment)
    assert json.loads((home / '.startup-legacy-reboot.json').read_bytes())['phase'] == 'committed'
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    value = drain_startup.begin()
    drain_startup.restore(hold_id=value['hold_id'])


def test_production_source_binding_tracks_helper_bytes(tmp_path, monkeypatch):
    from hermes_cli import startup_hold_reboot as reboot
    package = tmp_path / 'hermes_cli'
    package.mkdir()
    monkeypatch.setattr(reboot, '__file__', str(package / 'startup_hold_reboot.py'))
    helper = tmp_path / 'helper.py'
    helper.write_text('fixture = 1\n')
    first = reboot._source_binding()
    helper.write_text('fixture = 2\n')
    assert reboot._source_binding() != first


@pytest.mark.parametrize('kind', ['directory-link', 'file-link', 'fifo', 'oversized'])
def test_source_binding_rejects_incomplete_or_nonregular_inputs(tmp_path, monkeypatch, kind):
    from hermes_cli import startup_hold_reboot as reboot
    package = tmp_path / 'hermes_cli'
    package.mkdir()
    monkeypatch.setattr(reboot, '__file__', str(package / 'startup_hold_reboot.py'))
    target = tmp_path / 'helper.py'
    if kind == 'directory-link':
        (tmp_path / 'outside').mkdir()
        (tmp_path / 'pm').symlink_to(tmp_path / 'outside', target_is_directory=True)
    elif kind == 'file-link':
        target.symlink_to(tmp_path / 'missing')
    elif kind == 'fifo':
        os.mkfifo(target)
    else:
        target.write_bytes(b' ' * 4_194_305)
    with pytest.raises((RuntimeError, OSError)):
        reboot._source_binding()


def test_source_traversal_error_is_not_silently_omitted(tmp_path, monkeypatch):
    from hermes_cli import startup_hold_reboot as reboot
    def failed_walk(root, *, onerror):
        onerror(PermissionError('fixture'))
        return iter(())
    monkeypatch.setattr(reboot.os, 'walk', failed_walk)
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        reboot._source_binding()
