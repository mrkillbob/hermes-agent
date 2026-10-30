"""Command outcomes survive the Windows install fixture's existing artifact copy."""

import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.e2e.core.windows_update import _machine


@pytest.fixture
def machine(tmp_path, monkeypatch):
    machine = _machine.Machine(root=tmp_path / "machine", profile_name="synthetic", base_url="")
    machine.profile.mkdir(parents=True)
    monkeypatch.setattr(machine, "evidence", lambda: "synthetic fixture only")
    return machine


def _status(machine):
    path = machine.logs / "01-install.status.json"
    assert path.is_file(), "command status must survive alongside the transcript"
    return json.loads(path.read_text())


def test_command_exit_and_phase_evidence_reaches_existing_artifacts(machine, tmp_path, monkeypatch):
    run = machine._run_logged(
        [sys.executable, "-c", "print('node-deps: installing workspace dependencies with npm ci (ui-tui, web)...'); "
         "print('https://private.invalid/?token=sentinel'); raise SystemExit(7)"],
        "install", timeout=30,
    )
    assert run.returncode == 7
    assert "sentinel" in run.stdout  # The existing command result is preserved.
    detail = _status(machine)
    assert detail["returncode"] == 7
    assert detail["timed_out"] is False
    assert detail["termination_started"] is False
    assert detail["reader_finished"] is True
    assert detail["phases"][0]["phase"] == "npm_ci_started"
    assert 0 <= detail["phases"][0]["elapsed_s"] <= detail["elapsed_s"]
    assert "sentinel" not in json.dumps(detail)
    assert "private.invalid" not in json.dumps(detail)

    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("HERMES_E2E_ARTIFACTS", str(artifacts))
    monkeypatch.setattr(machine, "kill_owned", lambda: None)
    monkeypatch.setattr(_machine, "_restore_hkcu_path", lambda value: None)
    machine.teardown()
    copied = artifacts / machine.root.name / "transcripts" / "01-install.status.json"
    assert json.loads(copied.read_text()) == detail


class _TimeoutProcess:
    pid = 123
    returncode = None

    def __init__(self):
        self.drained = threading.Event()
        owner = self

        class Transcript(io.BytesIO):
            def readline(self, *args):
                raw = super().readline(*args)
                if not raw:
                    owner.drained.set()
                return raw

        self.stdout = Transcript(b"node-deps: installing workspace dependencies with npm ci (ui-tui, web)...\n"
                                 b"private sentinel environment or command\n")
        self.waits = []

    def wait(self, timeout):
        self.waits.append(timeout)
        if len(self.waits) == 1:
            assert self.drained.wait(5), "transcript pump did not drain"
            raise subprocess.TimeoutExpired("private sentinel command", timeout)
        self.returncode = 17
        return self.returncode


def test_timeout_preserves_existing_termination_and_numeric_status(machine, monkeypatch):
    proc = _TimeoutProcess()
    calls = []
    monkeypatch.setattr(_machine.subprocess, "Popen", lambda *args, **kwargs: proc)

    def taskkill(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 5, "private sentinel output", "private sentinel error")

    monkeypatch.setattr(_machine.subprocess, "run", taskkill)
    with pytest.raises(AssertionError, match="install did not finish within 1500s"):
        machine._run_logged(["unused"], "install", timeout=1500)

    assert proc.waits == [1500, 60]
    assert calls == [(["taskkill", "/PID", "123", "/T", "/F"],
                      {"capture_output": True, "timeout": 60, "check": False})]
    detail = _status(machine)
    assert detail["timed_out"] is True
    assert detail["timeout_s"] == 1500
    assert detail["observed_returncode_before_cleanup"] is None
    assert detail["termination_started"] is True
    assert detail["taskkill_returncode"] == 5
    assert detail["returncode"] == 17
    assert detail["reader_finished"] is True
    assert detail["phases"][-1]["phase"] == "npm_ci_started"
    assert "sentinel" not in json.dumps(detail)


def test_cleanup_exception_is_preserved_with_partial_termination_evidence(machine, monkeypatch):
    proc = _TimeoutProcess()
    original = subprocess.TimeoutExpired("private sentinel taskkill", 60)
    monkeypatch.setattr(_machine.subprocess, "Popen", lambda *args, **kwargs: proc)

    def taskkill(*args, **kwargs):
        raise original

    monkeypatch.setattr(_machine.subprocess, "run", taskkill)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        machine._run_logged(["unused"], "install", timeout=1500)

    assert caught.value is original
    detail = _status(machine)
    assert detail["termination_started"] is True
    assert detail["taskkill_returncode"] is None
    assert detail["returncode"] is None
    assert "sentinel" not in json.dumps(detail)


def test_status_write_failure_does_not_replace_command_result(machine, monkeypatch):
    original = Path.write_text

    def write_text(path, *args, **kwargs):
        if path.name.endswith(".status.json"):
            raise OSError("synthetic read-only diagnostics destination")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)
    run = machine._run_logged([sys.executable, "-c", "raise SystemExit(7)"], "install", timeout=30)
    assert run.returncode == 7
