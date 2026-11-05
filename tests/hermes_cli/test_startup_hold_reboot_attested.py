"""An attested exact checkpoint survives a ctime-only change and refuses every real one."""
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from tests.hermes_cli import test_startup_hold_reboot as fixtures

environment = fixtures.environment
prepare = fixtures.prepare
_AFTER = fixtures._AFTER
_BEFORE = fixtures._BEFORE

pytestmark = pytest.mark.platforms('posix')

from hermes_cli import startup_hold_reboot as _reboot

_REAL_SOURCE_BINDING = _reboot._source_binding  # before any fixture replaces it


@pytest.fixture
def attested(environment, monkeypatch, tmp_path):
    """A prepared schema-3 checkpoint, a retained source tree, and the later native boot."""
    from hermes_cli import startup_hold_reboot as reboot
    home, _ = environment
    source = tmp_path / 'retained-source'
    source.mkdir()
    (source / 'a.py').write_text('x = 1\n')

    def binding(root=None):
        base = Path(source if root is None else root)
        digest = hashlib.sha256(b''.join(p.read_bytes() for p in sorted(base.rglob('*.py')))).hexdigest()
        return dict(path=str(base), sha256=digest)

    monkeypatch.setattr(reboot, '_source_binding', binding)
    directory = home / '.startup-writers'
    directory.mkdir(mode=0o700)
    for index in range(2):
        (directory / f'{index}.hold').write_text(json.dumps(dict(
            schema=1, pid=800 + index, create_time=None, state='unsettled')))
    prepare(environment)
    checkpoint = home / reboot._FILENAME
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    return home, directory, source, digest


def _touch_ctime(path):
    before = path.lstat().st_ctime_ns
    os.chmod(path, stat.S_IMODE(path.lstat().st_mode))
    assert path.lstat().st_ctime_ns != before


def _held(home):
    return (home / '.startup-hold.json').exists()


def test_ctime_only_change_breaks_strict_but_not_attested_and_nothing_is_rewritten(attested):
    from gateway import drain_startup
    home, directory, _, digest = attested
    files = {p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in directory.iterdir()}
    ledger = (home / 'spawn-ledger.json').read_bytes()
    checkpoint = (home / '.startup-legacy-reboot.json').read_bytes()
    for path in directory.iterdir():
        _touch_ctime(path)
    with pytest.raises(RuntimeError, match='startup_hold_writers_busy|startup_hold_reboot_unavailable'):
        drain_startup.begin()
    assert not _held(home)
    value = drain_startup.begin(attested_sha256=digest)
    assert _held(home)
    _touch_ctime(directory / '0.hold')
    drain_startup.restore(hold_id=value['hold_id'], attested_sha256=digest)
    assert {p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in directory.iterdir()} == files
    assert (home / 'spawn-ledger.json').read_bytes() == ledger
    assert (home / '.startup-legacy-reboot.json').read_bytes() == checkpoint
    assert (home / 'ESTOP').exists()
    assert not _held(home)


def test_attested_still_accepts_an_untouched_checkpoint(attested):
    from gateway import drain_startup
    home, _, _, digest = attested
    value = drain_startup.begin(attested_sha256=digest)
    drain_startup.restore(hold_id=value['hold_id'], attested_sha256=digest)
    assert not _held(home)


@pytest.mark.parametrize('digest', ['0' * 64, 'f' * 64, 'not-a-digest', '', 'A' * 64])
def test_wrong_or_malformed_attestation_refuses_without_publishing(attested, digest):
    from gateway import drain_startup
    home, _, _, _ = attested
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin(attested_sha256=digest)
    assert not _held(home)


def test_changed_checkpoint_bytes_do_not_match_the_attested_hash(attested):
    from gateway import drain_startup
    home, _, _, digest = attested
    path = home / '.startup-legacy-reboot.json'
    record = json.loads(path.read_text())
    record['phase'] = 'committed'
    raw = json.dumps(record, sort_keys=True, indent=1).encode()
    path.write_bytes(raw)
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin(attested_sha256=digest)
    assert not _held(home)


def _same_size_content(directory, target):
    stamp = target.stat().st_mtime_ns
    target.write_bytes(target.read_bytes().replace(b'800', b'801'))
    os.utime(target, ns=(stamp, stamp))


def _add(directory, target):
    (directory / 'extra.hold').write_text(json.dumps(dict(schema=1, pid=9, create_time=None, state='unsettled')))


def _replace_inode(directory, target):
    replacement = directory / 'new.hold'
    replacement.write_bytes(target.read_bytes())
    os.utime(replacement, ns=(target.stat().st_mtime_ns,) * 2)
    replacement.chmod(0o600)
    target.unlink()
    replacement.rename(target)


def _grow(directory, target):
    target.write_text(json.dumps(dict(schema=1, pid=800, create_time=None, state='unsettled')) + ' ')


_REAL_CHANGES = {
    'content_same_size_and_mtime': _same_size_content,
    'add': _add,
    'remove': lambda directory, target: target.unlink(),
    'replace_inode': _replace_inode,
    'size': _grow,
}


@pytest.mark.parametrize('change', sorted(_REAL_CHANGES))
def test_real_writer_changes_refuse_even_when_attested(attested, change):
    from gateway import drain_startup
    home, directory, _, digest = attested
    _REAL_CHANGES[change](directory, directory / '0.hold')
    with pytest.raises(RuntimeError, match='startup_hold_writers_busy|startup_hold_reboot_unavailable'):
        drain_startup.begin(attested_sha256=digest)
    assert not _held(home)


def test_same_boot_is_never_attested(attested, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import startup_hold_reboot as reboot
    home, _, _, digest = attested
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_BEFORE))
    with pytest.raises(RuntimeError, match='startup_hold_reboot_required'):
        drain_startup.begin(attested_sha256=digest)
    assert not _held(home)


@pytest.mark.parametrize('change', ['edit', 'missing'])
def test_retained_recovery_source_must_still_match(attested, change):
    from gateway import drain_startup
    home, _, source, digest = attested
    if change == 'edit':
        (source / 'a.py').write_text('x = 2\n')
    else:
        (source / 'a.py').unlink()
        source.rmdir()
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin(attested_sha256=digest)
    assert not _held(home)


def test_ledger_remains_strict_under_attestation(attested):
    from gateway import drain_startup
    home, _, _, digest = attested
    _touch_ctime(home / 'spawn-ledger.json')
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin(attested_sha256=digest)
    assert not _held(home)


def test_pause_is_required_and_a_live_gateway_blocks(attested, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import dashboard_procs
    home, _, _, digest = attested
    monkeypatch.setattr(dashboard_procs, '_iter_process_table',
                        lambda **kwargs: [(os.getpid() + 1, 'python hermes gateway run')])
    monkeypatch.setattr('hermes_cli.startup_hold_writers._same_process_root', lambda pid: True)
    with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
        drain_startup.begin(attested_sha256=digest)
    monkeypatch.setattr(dashboard_procs, '_iter_process_table', lambda **kwargs: [])
    (home / 'ESTOP').unlink()
    with pytest.raises(Exception, match='startup_hold_requires_pause'):
        drain_startup.begin(attested_sha256=digest)
    assert not _held(home)


def test_cli_exposes_the_attestation_only_on_hold_and_restore():
    import argparse
    from hermes_cli import gateway_kanban_window as window
    parser = argparse.ArgumentParser()
    window.add_parser(parser.add_subparsers())
    hold = parser.parse_args(['drain', 'hold', '--attest-checkpoint-sha256', 'a' * 64])
    assert hold.attest_checkpoint_sha256 == 'a' * 64
    restore = parser.parse_args(['drain', 'restore', '--hold-id', 'x', '--attest-checkpoint-sha256', 'b' * 64])
    assert restore.attest_checkpoint_sha256 == 'b' * 64
    assert parser.parse_args(['drain', 'hold']).attest_checkpoint_sha256 is None
    with pytest.raises(SystemExit):
        parser.parse_args(['drain', 'begin', '--task-id', 't', '--attest-checkpoint-sha256', 'a' * 64])


def test_same_size_checkpoint_rewrite_with_restored_mtime_fails_the_next_check(attested):
    from hermes_cli import startup_hold_reboot_attested as module
    home, _, _, digest = attested
    path = home / '.startup-legacy-reboot.json'
    with module.writers() as (_, snapshot):
        with module.exclusion(writers=snapshot, attested_sha256=digest) as proof:
            stamp = path.stat().st_mtime_ns
            raw = path.read_bytes()
            index = raw.index(b'"id": "') + 7
            swapped = raw[:index] + (b'0' if raw[index:index + 1] != b'0' else b'1') + raw[index + 1:]
            assert len(swapped) == len(raw) and swapped != raw
            with open(path, 'r+b') as handle:
                handle.write(swapped)
            os.utime(path, ns=(stamp, stamp))
            with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
                proof.check()
            path.write_bytes(raw)
            os.utime(path, ns=(stamp, stamp))
            proof.check()


def test_schema2_checkpoint_is_not_attestable(environment, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import startup_hold_reboot as reboot
    home, _ = environment
    prepare(environment)
    digest = hashlib.sha256((home / '.startup-legacy-reboot.json').read_bytes()).hexdigest()
    assert json.loads((home / '.startup-legacy-reboot.json').read_text())['schema'] == 2
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin(attested_sha256=digest)
    assert not _held(home)


def test_checkpoint_held_by_another_process_refuses(attested, monkeypatch):
    from gateway import drain_startup
    from pm import lifetime_handles
    home, _, _, digest = attested
    real = lifetime_handles.open_hold
    monkeypatch.setattr(lifetime_handles, 'open_hold',
                        lambda path, **kwargs: None if Path(path).name == '.startup-legacy-reboot.json' else real(path, **kwargs))
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        drain_startup.begin(attested_sha256=digest)
    assert not _held(home)


def test_real_source_binding_follows_included_files_only(tmp_path):
    root = tmp_path / 'tree'
    (root / 'sub').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'venv').mkdir()
    (root / 'a.py').write_text('x = 1\n')
    (root / 'sub' / 'b.sh').write_text('echo b\n')
    first = _REAL_SOURCE_BINDING(root)
    assert first['path'] == str(root)
    (root / 'tests' / 'skipped.py').write_text('ignored\n')
    (root / 'venv' / 'skipped.py').write_text('ignored\n')
    (root / 'notes.txt').write_text('not a source suffix\n')
    assert _REAL_SOURCE_BINDING(root) == first
    (root / 'a.py').write_text('x = 2\n')
    assert _REAL_SOURCE_BINDING(root) != first
    (root / 'link').symlink_to(tmp_path)
    with pytest.raises(RuntimeError, match='startup_hold_reboot_unavailable'):
        _REAL_SOURCE_BINDING(root)


def test_cli_normalizes_the_digest_and_requires_a_hold_id(attested):
    import argparse
    from hermes_cli import gateway_kanban_window as window
    home, _, _, digest = attested
    upper = argparse.Namespace(drain_action='hold', attest_checkpoint_sha256=digest.upper())
    status = window.execute(upper)
    assert status['held'] is True
    restored = window.execute(argparse.Namespace(drain_action='restore', hold_id=status['hold_id'],
                                                 window_id=None, attest_checkpoint_sha256=digest.upper()))
    assert restored == {'restored': True}
    with pytest.raises(ValueError, match='requires --hold-id'):
        window.execute(argparse.Namespace(drain_action='restore', hold_id=None, window_id='w',
                                          attest_checkpoint_sha256=digest))
    with pytest.raises(ValueError, match='64-character'):
        window.execute(argparse.Namespace(drain_action='hold', attest_checkpoint_sha256='abc'))
    assert not _held(home)
