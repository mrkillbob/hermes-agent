"""Fault and actual-process contracts for the startup writer boundary."""
import json

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    return tmp_path


@pytest.mark.parametrize('record', ['[]', 'null', '1'])
def test_non_object_reference_is_an_explicit_refusal(home, record):
    from hermes_cli import startup_hold
    directory = home / '.startup-writers'
    directory.mkdir()
    (directory / 'fixture.hold').write_text(record)
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            with startup_hold.writer_exclusion():
                pytest.fail('invalid reference admitted')


@pytest.mark.platforms('posix')
def test_writer_directory_cannot_redirect_inventory(home):
    from hermes_cli import startup_hold
    target = home / 'unrelated'
    target.mkdir()
    (home / '.startup-writers').symlink_to(target, target_is_directory=True)
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            with startup_hold.writer_exclusion():
                pytest.fail('redirected inventory admitted')


def test_unknown_child_departure_is_scoped_to_its_root(home, tmp_path, monkeypatch):
    from hermes_cli import startup_hold
    startup_hold.mark_uncertain_children()
    other = tmp_path / 'different-root'
    monkeypatch.setenv('HERMES_HOME', str(other))
    monkeypatch.setattr(startup_hold.threading, 'enumerate', list)
    import psutil
    monkeypatch.setattr(psutil.Process, 'children', lambda *args, **kwargs: [])
    with startup_hold.writer_scope():
        pass
    record = json.loads(next((other / '.startup-writers').iterdir()).read_text())
    assert record['state'] == 'quiescent'


def test_failed_child_inventory_retains_uncertainty(home, monkeypatch):
    from hermes_cli import startup_hold
    monkeypatch.setattr(startup_hold.threading, 'enumerate', list)
    import psutil
    def inaccessible(*args, **kwargs):
        raise psutil.AccessDenied()
    monkeypatch.setattr(psutil.Process, 'children', inaccessible)
    with startup_hold.writer_scope():
        pass
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            with startup_hold.writer_exclusion():
                pytest.fail('unknown child inventory admitted')
