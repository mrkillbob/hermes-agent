"""Prospective reboot proves old writer extinction without rewriting retained debt."""
import json
import os

import pytest

from tests.hermes_cli import test_startup_hold_reboot as fixtures

environment = fixtures.environment
prepare = fixtures.prepare
_AFTER = fixtures._AFTER

pytestmark = pytest.mark.platforms('posix')


def _writers(home):
    directory = home / '.startup-writers'
    directory.mkdir(mode=0o700)
    for index in range(2):
        (directory / f'{index}.hold').write_text(json.dumps(dict(
            schema=1, pid=800 + index, create_time=None, state='unsettled')))
    return directory


def test_legacy_unsettled_records_require_new_boot_and_remain_intact(environment, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import startup_hold, startup_hold_reboot as reboot
    home, _ = environment
    directory = _writers(home)
    before = {p.name: (p.read_bytes(), p.stat().st_ino) for p in directory.iterdir()}
    with pytest.raises(RuntimeError, match='writers_busy'):
        with startup_hold.writer_exclusion():
            pytest.fail('ordinary exclusion cannot settle unknown descendants')
    result = prepare(environment)
    assert result['admission_granted'] is False
    with pytest.raises(RuntimeError, match='reboot_required'):
        drain_startup.begin()
    assert not (home / '.startup-hold.json').exists()
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    value = drain_startup.begin()
    drain_startup.restore(hold_id=value['hold_id'])
    assert {p.name: (p.read_bytes(), p.stat().st_ino) for p in directory.iterdir()} == before
    assert (home / 'ESTOP').exists()
    with pytest.raises(RuntimeError, match='writers_busy'):
        with startup_hold.writer_exclusion():
            pytest.fail('checkpoint must not weaken ordinary exclusion')


@pytest.mark.parametrize('change', ['bytes', 'replace', 'add', 'remove', 'directory', 'host', 'volume', 'incomplete'])
def test_checkpoint_refuses_changed_inventory_or_authority(environment, monkeypatch, change):
    from gateway import drain_startup
    from hermes_cli import startup_hold_reboot as reboot
    home, _ = environment
    directory = _writers(home)
    prepare(environment)
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    path = directory / '0.hold'
    def replace():
        other = home / 'replacement'
        other.write_bytes(path.read_bytes())
        os.replace(other, path)

    def directory_change():
        directory.rename(home / 'old-writers')
        directory.mkdir(mode=0o700)

    def incomplete():
        checkpoint = home / reboot._FILENAME
        record = json.loads(checkpoint.read_bytes())
        record['phase'] = 'preparing'
        checkpoint.write_text(json.dumps(record))

    actions = {
        'bytes': lambda: path.write_bytes(path.read_bytes() + b' '),
        'replace': replace,
        'add': lambda: (directory / 'new.hold').write_bytes(path.read_bytes()),
        'remove': path.unlink,
        'directory': directory_change,
        'host': lambda: monkeypatch.setattr(reboot, '_native_identity',
            lambda: dict(_AFTER, host='66666666-6666-4666-8666-666666666666')),
        'volume': lambda: monkeypatch.setattr(reboot, '_native_volume',
            lambda fd: '66666666-6666-4666-8666-666666666666'),
        'incomplete': incomplete,
    }
    actions[change]()
    with pytest.raises((RuntimeError, OSError)):
        drain_startup.begin()
    assert not (home / '.startup-hold.json').exists()


@pytest.mark.parametrize('kind', ['held', 'symlink', 'hardlink', 'malformed'])
def test_prepare_refuses_unowned_or_unreadable_references(environment, kind):
    from pm.lifetime_handles import open_hold
    home, _ = environment
    directory = _writers(home)
    path = directory / '0.hold'
    fd = None
    if kind == 'held':
        fd = open_hold(path)
    elif kind == 'symlink':
        path.unlink()
        path.symlink_to(directory / '1.hold')
    elif kind == 'hardlink':
        os.link(path, home / 'linked')
    else:
        path.write_bytes(b'broken')
    try:
        with pytest.raises((RuntimeError, OSError)):
            prepare(environment)
        assert not (home / '.startup-legacy-reboot.json').exists()
    finally:
        if fd is not None:
            os.close(fd)


@pytest.mark.parametrize('change', ['remove', 'downgrade'])
def test_checkpoint_selection_cannot_outlive_its_authority(environment, monkeypatch, change):
    from gateway import drain_startup
    from hermes_cli import startup_hold_reboot as reboot, startup_hold_writer_checkpoint as writers
    home, _ = environment
    _writers(home)
    prepare(environment)
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    original = writers._has_writer_checkpoint

    def change_after_selection():
        selected = original()
        path = home / reboot._FILENAME
        if change == 'remove':
            path.unlink()
        else:
            record = json.loads(path.read_bytes())
            record['schema'] = 2
            record['binding'].pop('writers')
            path.write_text(json.dumps(record))
        return selected

    monkeypatch.setattr(writers, '_has_writer_checkpoint', change_after_selection)
    with pytest.raises(RuntimeError, match='reboot_unavailable'):
        drain_startup.begin()
    assert not (home / '.startup-hold.json').exists()


def test_schema3_retains_exact_ordinary_settled_coverage(environment, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import startup_hold_reboot as reboot
    home, entries = environment
    directory = _writers(home)
    (directory / 'settled.hold').write_text(json.dumps(dict(
        schema=1, pid=900, create_time=900.25, state='exit_ready')))
    entries.append(dict(pid=900, create_time=900.25, purpose='serve'))
    (home / 'spawn-ledger.json').write_text(json.dumps(entries))
    prepare(environment)
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    value = drain_startup.begin()
    drain_startup.restore(hold_id=value['hold_id'])


def test_partial_read_cannot_commit_a_prefix_as_complete_writer_evidence(environment, monkeypatch):
    from hermes_cli.startup_hold_writer_checkpoint import capture_writers
    home, _ = environment
    directory = _writers(home)
    path = directory / '0.hold'
    raw = path.read_bytes()
    path.write_bytes(raw + b' ')
    original = os.read

    def partial(fd, size):
        value = original(fd, size)
        return raw if value == raw + b' ' else value

    monkeypatch.setattr(os, 'read', partial)
    with pytest.raises(RuntimeError, match='writers_busy'):
        with capture_writers():
            pytest.fail('partial record bytes cannot become checkpoint evidence')
