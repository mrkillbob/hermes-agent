"""Dashboard actions recover from a Windows job that forbids breakaway (fork #160)."""

from unittest.mock import Mock

import pytest

from hermes_cli import web_server_gateway as gateway
from hermes_cli._subprocess_compat import windows_detach_flags, windows_detach_flags_without_breakaway


@pytest.fixture
def action_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway, "_ACTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(gateway, "_ACTION_PROCS", {})
    monkeypatch.setattr(gateway, "_ACTION_RESULTS", {})
    monkeypatch.setattr(gateway, "_ACTION_COMMANDS", {})
    monkeypatch.setattr(gateway, "_ACTION_IDS", {})
    monkeypatch.setattr(gateway, "_profile_action_environment", lambda *_args: {"HERMES_NONINTERACTIVE": "1"})
    monkeypatch.setattr(gateway, "_action_targets_system_gateway", lambda *_args: False)
    monkeypatch.setattr("hermes_cli._launchers.runtime_command", lambda *_args: ["python", "-I", "-c", "pass"])
    return tmp_path


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("winerror", [5, 2])
def test_native_windows_only_retries_denied_job_breakaway(monkeypatch, action_environment, caplog, winerror):
    denied = OSError("spawn refused")
    denied.winerror = winerror
    child = Mock(pid=42)
    spawn = Mock(side_effect=[denied, child])
    monkeypatch.setattr(gateway.subprocess, "Popen", spawn)

    if winerror == 5:
        assert gateway._spawn_hermes_action(["gateway", "run"], "gateway-start") is child
        first, second = spawn.call_args_list
        assert first.kwargs["creationflags"] == windows_detach_flags()
        assert second.kwargs["creationflags"] == windows_detach_flags_without_breakaway()
        assert first.args == second.args
        assert first.kwargs["env"] == second.kwargs["env"]
        assert "parent process exits" in caplog.text
        assert "parent process exits" in (action_environment / "gateway-start.log").read_text()
    else:
        with pytest.raises(OSError) as caught:
            gateway._spawn_hermes_action(["gateway", "run"], "gateway-start")
        assert caught.value is denied
        assert spawn.call_count == 1
    assert spawn.call_args.kwargs["stdout"].closed


def test_failed_action_closes_log_without_recording_child(monkeypatch, action_environment):
    spawn = Mock(side_effect=FileNotFoundError("missing interpreter"))
    monkeypatch.setattr(gateway.subprocess, "Popen", spawn)

    with pytest.raises(FileNotFoundError):
        gateway._spawn_hermes_action(["gateway", "run"], "gateway-start")

    assert spawn.call_count == 1
    assert spawn.call_args.kwargs["stdout"].closed
    assert "gateway-start" not in gateway._ACTION_PROCS
