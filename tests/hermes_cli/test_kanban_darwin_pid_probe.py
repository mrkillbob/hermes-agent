"""A failing Darwin zombie probe must not invent a dead worker."""
import subprocess
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.platforms("macos")


@pytest.mark.parametrize("status", [1, 2, 127, -9, -11])
def test_failed_ps_keeps_independently_live_process(monkeypatch, status):
    from gateway import status as gateway_status
    from hermes_cli import kanban_db_dispatch as dispatch

    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=status, stdout=""))
    assert dispatch._pid_alive(123456789) is True


def test_failed_ps_confirms_process_disappearance(monkeypatch):
    from gateway import status as gateway_status
    from hermes_cli import kanban_db_dispatch as dispatch

    values = iter([True, False])
    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: next(values))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    assert dispatch._pid_alive(123456789) is False


def test_initial_absence_skips_ps(monkeypatch):
    from gateway import status as gateway_status
    from hermes_cli import kanban_db_dispatch as dispatch

    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("absent PID reached ps"))
    assert dispatch._pid_alive(123456789) is False


def test_failed_ps_output_is_not_interpreted_as_zombie(monkeypatch):
    from gateway import status as gateway_status
    from hermes_cli import kanban_db_dispatch as dispatch

    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="Z"))
    assert dispatch._pid_alive(123456789) is True


@pytest.mark.parametrize("state,expected", [("Z", False), ("S", True), ("", True)])
def test_successful_ps_keeps_zombie_semantics(monkeypatch, state, expected):
    from gateway import status as gateway_status
    from hermes_cli import kanban_db_dispatch as dispatch

    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=state))
    assert dispatch._pid_alive(123456789) is expected


def test_timeout_preserves_initial_live_probe(monkeypatch):
    from gateway import status as gateway_status
    from hermes_cli import kanban_db_dispatch as dispatch

    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: True)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ps", 1)
    monkeypatch.setattr(subprocess, "run", timeout)
    assert dispatch._pid_alive(123456789) is True
