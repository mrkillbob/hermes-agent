"""Explicit obsolete checkpoint supersession preserves evidence and crash refusal."""
import hashlib
import json
import os

import pytest

from tests.hermes_cli import test_startup_hold_reboot as fixtures
from tests.hermes_cli.test_startup_hold_writer_checkpoint import _writers

environment = fixtures.environment
pytestmark = pytest.mark.platforms('posix')


@pytest.fixture
def obsolete(environment):
    from hermes_cli import startup_hold_reboot as reboot
    home, entries = environment
    fixtures.prepare(environment)
    path = home / reboot._FILENAME
    raw = path.read_bytes()
    old_entries = json.loads((home / 'spawn-ledger.json').read_bytes())
    for row in entries[:3]:
        row['pid'] += 1000
        row['create_time'] += 1000
    (home / 'spawn-ledger.json').write_text(json.dumps(entries))
    _writers(home)
    return home, entries, raw, old_entries


def _prepare(obsolete, expected=None):
    from hermes_cli import startup_hold_reboot as reboot
    home, entries, raw, _ = obsolete
    import argparse
    from hermes_cli import gateway_kanban_window
    parser = argparse.ArgumentParser()
    gateway_kanban_window.add_parser(parser.add_subparsers())
    argv = ['drain', 'prepare-reboot-reconcile', '--ledger-sha256',
            hashlib.sha256((home / 'spawn-ledger.json').read_bytes()).hexdigest(),
            '--install-id', '0fcc25fdfa9e', '--spawner-pid', '50', '--spawner-birth', '50.25',
            '--supersede-sha256', expected or hashlib.sha256(raw).hexdigest()]
    for entry in entries[:3]:
        argv.extend(['--entry-sha256', reboot._hash(entry)])
    return gateway_kanban_window.execute(parser.parse_args(argv))



def test_explicit_supersession_preserves_old_bytes_and_requires_future_boot(obsolete, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import startup_hold_reboot as reboot
    home, _, raw, _ = obsolete
    result = _prepare(obsolete)
    assert (home / result['retained_checkpoint']).read_bytes() == raw
    assert not result['admission_granted'] and not result['ledger_changed']
    with pytest.raises(RuntimeError, match='reboot_required'):
        drain_startup.begin()
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(fixtures._AFTER))
    hold = drain_startup.begin()
    drain_startup.restore(hold_id=hold['hold_id'])
    assert (home / result['retained_checkpoint']).read_bytes() == raw
    assert len(list((home / '.startup-writers').iterdir())) == 2


@pytest.mark.parametrize('failure', ['hash', 'old-rows', 'held', 'history-collision', 'permission'])
def test_supersession_refuses_without_replacing_old_checkpoint(obsolete, monkeypatch, failure):
    from pm.lifetime_handles import open_hold
    from hermes_cli import startup_hold_reboot as reboot, startup_hold_reboot_supersede as supersede
    home, entries, raw, old_entries = obsolete
    path = home / reboot._FILENAME
    fd = None
    expected = None
    if failure == 'hash':
        path.write_bytes(raw + b' ')
    if failure == 'old-rows':
        (home / 'spawn-ledger.json').write_text(json.dumps(entries + old_entries[:3]))
    if failure == 'held':
        fd = open_hold(path)
    if failure == 'history-collision':
        original = supersede._retain_history

        def collision(target, data):
            target.write_bytes(b'prior retained evidence')
            original(target, data)

        monkeypatch.setattr(supersede, '_retain_history', collision)
    if failure == 'permission':
        def denied(*args):
            raise PermissionError('synthetic denied history creation')
        monkeypatch.setattr(supersede, '_retain_history', denied)
    before = path.read_bytes()
    try:
        with pytest.raises((RuntimeError, OSError)):
            _prepare(obsolete, expected)
        assert path.read_bytes() == before
    finally:
        if fd is not None:
            os.close(fd)


@pytest.mark.parametrize('phase', ['before-promotion', 'after-promotion'])
def test_interrupted_supersession_never_grants_admission(obsolete, monkeypatch, phase):
    from gateway import drain_startup
    from hermes_cli import startup_hold_reboot as reboot
    home, _, raw, _ = obsolete
    original = reboot._write_record

    def interrupted(fd, record):
        if phase == 'after-promotion' and record['phase'] == 'committed':
            raise OSError('synthetic crash after promotion')
        original(fd, record)
        if phase == 'before-promotion' and record['phase'] == 'preparing':
            raise OSError('synthetic crash before promotion')

    monkeypatch.setattr(reboot, '_write_record', interrupted)
    with pytest.raises(OSError, match='synthetic crash'):
        _prepare(obsolete)
    histories = list(home.glob('.startup-legacy-reboot.json.*.history'))
    assert len(histories) == 1 and histories[0].read_bytes() == raw
    path = home / reboot._FILENAME
    if phase == 'before-promotion':
        assert path.read_bytes() == raw
    else:
        assert json.loads(path.read_bytes())['phase'] == 'preparing'
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(fixtures._AFTER))
    with pytest.raises(RuntimeError):
        drain_startup.begin()
    assert not (home / '.startup-hold.json').exists()


def test_stage_permissions_survive_restrictive_creation(obsolete, monkeypatch):
    import stat
    from pm import lifetime_handles
    from hermes_cli import startup_hold_reboot as reboot
    original = lifetime_handles.open_hold

    def restrictive(path, **kwargs):
        fd = original(path, **kwargs)
        if kwargs.get('create') and path.name.endswith('.preparing'):
            os.fchmod(fd, 0)
        return fd

    monkeypatch.setattr(lifetime_handles, 'open_hold', restrictive)
    result = _prepare(obsolete)
    home = obsolete[0]
    assert stat.S_IMODE((home / reboot._FILENAME).stat().st_mode) == 0o600
    assert stat.S_IMODE((home / result['retained_checkpoint']).stat().st_mode) == 0o600
