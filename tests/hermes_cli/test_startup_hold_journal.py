"""Durable targetless holds survive marker loss and interrupted marker transactions."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    from gateway import drain_startup
    monkeypatch.setattr(drain_startup, '_refuse_legacy_writers', lambda *args: None)
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: Path(os.environ['HERMES_HOME']))
    (tmp_path / 'ESTOP').touch()
    return tmp_path


@pytest.mark.parametrize('replacement', [None, '{"action":"drain"}'])
def test_lost_or_replaced_marker_remains_held_in_a_new_process(home, replacement):
    from gateway import drain_startup, drain_control
    drain_startup.begin()
    marker = home / '.drain_request.json'
    if replacement is None:
        marker.unlink()
    else:
        marker.write_text(replacement)
    child = subprocess.run([sys.executable, '-c',
        'from hermes_cli.startup_hold import active; print(active())'],
        cwd=Path(__file__).resolve().parents[2], env={**os.environ, 'HERMES_HOME': str(home)},
        text=True, capture_output=True, timeout=10, check=False)
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == 'True'
    before = marker.read_bytes() if marker.exists() else None
    assert not drain_control.clear_drain_request(home=home)
    with pytest.raises(RuntimeError):
        drain_control.write_drain_request(home=home)
    assert (marker.read_bytes() if marker.exists() else None) == before


def test_failed_marker_publication_keeps_recoverable_preparing_journal(home, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import startup_hold
    import utils
    marker = home / '.drain_request.json'
    prior = b'{"action":"drain","principal":"prior"}\r\n'
    marker.write_bytes(prior)
    write = utils.atomic_json_write
    def fail_marker(path, *args, **kwargs):
        if Path(path) == marker:
            raise OSError('fixture marker publication failed')
        return write(path, *args, **kwargs)
    monkeypatch.setattr(utils, 'atomic_json_write', fail_marker)
    with pytest.raises(OSError):
        drain_startup.begin()
    assert startup_hold.active()
    status = drain_startup.status()
    assert status['held'] and not status['armed']
    drain_startup.restore(hold_id=status['hold_id'])
    assert marker.read_bytes() == prior
    assert not startup_hold.active()


def test_restore_failure_is_held_and_retryable_under_original_identity(home, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import startup_hold
    import utils
    marker = home / '.drain_request.json'
    prior = b'{"action":"drain"}\n'
    marker.write_bytes(prior)
    value = drain_startup.begin()
    write = utils.atomic_write_bytes
    def fail_after_write(*args, **kwargs):
        write(*args, **kwargs)
        raise OSError('fixture failure after prior state restoration')
    monkeypatch.setattr(utils, 'atomic_write_bytes', fail_after_write)
    with pytest.raises(OSError):
        drain_startup.restore(hold_id=value['hold_id'])
    assert startup_hold.active()
    assert marker.read_bytes() == prior
    monkeypatch.setattr(utils, 'atomic_write_bytes', write)
    drain_startup.restore(hold_id=value['hold_id'])
    assert not startup_hold.active()
    assert marker.read_bytes() == prior
