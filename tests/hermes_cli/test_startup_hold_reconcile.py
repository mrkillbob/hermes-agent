"""Offline reconciliation preserves legacy debt, uncertain identities and wire privacy."""
import argparse
import hashlib
import json
import os

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    from hermes_cli import dashboard_procs
    path = tmp_path / 'home'
    path.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(path))
    monkeypatch.setattr(dashboard_procs, '_iter_process_table', lambda **kwargs: [])
    (path / 'ESTOP').touch()
    return path


def row():
    return dict(pid=1234, create_time=123.25, purpose='mcp-helper', install='legacy',
                spawner_pid=4321, spawner_create=122.25)


def save(home, entries):
    path = home / 'spawn-ledger.json'
    path.write_text(json.dumps(entries))
    return path.read_bytes()


@pytest.mark.parametrize('alive,expected', [(False, 'closure_required'), (True, 'busy'), (None, 'unknown')])
def test_repeated_reconciliation_preserves_rows(home, monkeypatch, alive, expected):
    from hermes_cli import process_identity, startup_hold_reconcile as reconcile
    monkeypatch.setattr(process_identity, '_pid_alive_matches', lambda *args, **kwargs: alive)
    before = save(home, [row(), dict(purpose='interactive', argv='private fixture')])
    for _ in range(2):
        result = reconcile.assess()
        assert result['counts'][expected] == 1
        assert result['counts']['unrelated'] == 1
        assert result['ledger_sha256'] == hashlib.sha256(before).hexdigest()
        assert result['admission_granted'] is False
        assert result['mode'] == 'assessment_only'
        assert 'private fixture' not in json.dumps(result)
        assert (home / 'spawn-ledger.json').read_bytes() == before
    assert not (home / '.drain_request.json').exists()
    assert not (home / '.startup-hold.json').exists()


@pytest.mark.parametrize('identity', [(4321, 122.25), (1234, 121.25), (1234, 124.25)])
def test_parent_or_other_incarnation_cannot_cover_helper(home, monkeypatch, identity):
    from hermes_cli import process_identity, startup_hold_reconcile as reconcile
    monkeypatch.setattr(process_identity, '_pid_alive_matches', lambda *args, **kwargs: False)
    assert reconcile.entry_state(row(), {identity}) == 'closure_required'


@pytest.mark.parametrize('birth', [None, float('nan'), float('inf'), -1, True, '123.25', 10**1000])
def test_ambiguous_identity_is_never_covered(home, birth):
    from hermes_cli.startup_hold_reconcile import entry_state
    entry = row()
    entry['create_time'] = birth
    assert entry_state(entry, ()) == 'unknown'


def test_receipt_cannot_override_live_or_unknown_identity(home, monkeypatch):
    from hermes_cli import process_identity, startup_hold_reconcile as reconcile
    for live, expected in ((True, 'busy'), (None, 'unknown'), (False, 'covered')):
        monkeypatch.setattr(process_identity, '_pid_alive_matches', lambda *args, **kwargs: live)
        assert reconcile.entry_state(row(), {(1234, 123.25)}) == expected


@pytest.mark.parametrize('content', [b'', b'{}', b'[1]', b'not-json', b'\xff', b' ' * 4_194_305])
def test_bad_history_refuses_without_repair(home, content):
    from hermes_cli.startup_hold_reconcile import assess
    path = home / 'spawn-ledger.json'
    path.write_bytes(content)
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        assess()
    assert path.read_bytes() == content
    assert not path.with_suffix('.json.corrupt').exists()


@pytest.mark.platforms('posix')
def test_link_and_fifo_history_refuse(home):
    from hermes_cli.startup_hold_reconcile import assess
    path = home / 'spawn-ledger.json'
    other = home / 'other'
    other.write_text('[]')
    path.symlink_to(other)
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        assess()
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        assess()
    assert other.read_text() == '[]'


def test_changed_snapshot_refuses(home, monkeypatch):
    from hermes_cli import process_identity, startup_hold_reconcile as reconcile
    before = save(home, [row()])
    def probe(*args, **kwargs):
        save(home, [row(), dict(purpose='interactive')])
        return False
    monkeypatch.setattr(process_identity, '_pid_alive_matches', probe)
    with pytest.raises(RuntimeError, match='startup_hold_ledger_changed'):
        reconcile.assess()
    assert (home / 'spawn-ledger.json').read_bytes() != before


def test_admission_and_writer_locks_are_required(home):
    from hermes_cli import startup_hold, startup_hold_reconcile as reconcile
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            reconcile.assess()
    with startup_hold.writer_scope():
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            reconcile.assess()


def test_reconciliation_requires_estop_and_defers_boot(home):
    from hermes_cli import startup_hold, startup_hold_reconcile as reconcile
    assert startup_hold.defer_maintenance(['gateway', 'drain', 'reconcile'])
    (home / 'ESTOP').unlink()
    with pytest.raises(RuntimeError, match='startup_hold_requires_pause'):
        reconcile.assess()
    assert not (home / '.startup-hold-admission.lock').exists()


@pytest.mark.parametrize('message,expected', [
    ('startup_hold_writers_unknown', 'startup_hold_writers_unknown'),
    ('startup_hold_writers_busy', 'startup_hold_writers_busy'),
    ('startup_hold_ledger_changed', 'startup_hold_ledger_changed'),
    ('private fixture details', 'kanban_window_refused'),
    ({'private': 'fixture'}, 'kanban_window_refused'),
])
def test_cli_only_preserves_bounded_plain_refusals(home, monkeypatch, capsys, message, expected):
    from hermes_cli import gateway_kanban_window as cli
    def fail(_args):
        raise RuntimeError(message)
    monkeypatch.setattr(cli, 'execute', fail)
    with pytest.raises(SystemExit) as stopped:
        cli.cmd_gateway_drain(argparse.Namespace(drain_action='reconcile'))
    assert stopped.value.code == 6
    assert json.loads(capsys.readouterr().out) == {'error': expected}


def test_parser_dispatches_assessment(home):
    from hermes_cli import gateway_kanban_window as cli
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers())
    args = parser.parse_args(['drain', 'reconcile'])
    assert cli.execute(args)['admission_granted'] is False


def test_old_boot_metadata_does_not_create_closure(home, monkeypatch):
    from hermes_cli import process_identity, startup_hold_reconcile as reconcile
    monkeypatch.setattr(process_identity, '_pid_alive_matches', lambda *args, **kwargs: False)
    entry = row()
    entry['boot_id'] = 'previous-boot-fixture'
    save(home, [entry])
    assert reconcile.assess()['counts']['closure_required'] == 1


def test_foreign_home_and_unrelated_rows_are_retained(home, monkeypatch, tmp_path):
    from hermes_cli import process_identity, startup_hold_reconcile as reconcile
    def unexpected_probe(*args, **kwargs):
        pytest.fail('foreign rows must not be probed or adopted')
    monkeypatch.setattr(process_identity, '_pid_alive_matches', unexpected_probe)
    entry = row()
    entry['hermes_home'] = str(tmp_path / 'foreign-home')
    before = save(home, [entry, dict(purpose='interactive')])
    result = reconcile.assess()
    assert result['counts']['foreign'] == 1
    assert result['counts']['unrelated'] == 1
    assert (home / 'spawn-ledger.json').read_bytes() == before


def test_failed_live_census_never_returns_an_assessment(home, monkeypatch):
    from hermes_cli import dashboard_procs, startup_hold_reconcile as reconcile
    def unavailable(**kwargs):
        raise RuntimeError('startup_hold_writers_unknown')
    monkeypatch.setattr(dashboard_procs, '_iter_process_table', unavailable)
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        reconcile.assess()


@pytest.mark.parametrize('purpose', [[], {}, None, 1])
def test_malformed_purpose_is_bounded_unknown(home, purpose):
    from hermes_cli.startup_hold_reconcile import entry_state
    entry = row()
    entry['purpose'] = purpose
    assert entry_state(entry, ()) == 'unknown'


def test_short_reads_do_not_accept_valid_prefix(home, monkeypatch):
    from hermes_cli import startup_hold_reconcile as reconcile
    save(home, [row()])
    original = os.read
    monkeypatch.setattr(reconcile.os, 'read', lambda fd, size: original(fd, min(size, 2)))
    digest, entries = reconcile.ledger_snapshot()
    assert entries == [row()]
    assert digest == hashlib.sha256((home / 'spawn-ledger.json').read_bytes()).hexdigest()


def test_concurrent_write_during_snapshot_refuses(home, monkeypatch):
    from hermes_cli import startup_hold_reconcile as reconcile
    save(home, [row()])
    original = reconcile._read_complete
    def changed(fd):
        raw = original(fd)
        save(home, [])
        return raw
    monkeypatch.setattr(reconcile, '_read_complete', changed)
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        reconcile.ledger_snapshot()
