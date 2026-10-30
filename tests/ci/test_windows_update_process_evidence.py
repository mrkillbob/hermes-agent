"""Cleanup evidence distinguishes a kill request from original-identity exit."""

import json

import psutil
import pytest

from tests.e2e.core.windows_update._machine import Machine


class OwnedProcess:
    pid = 431

    def __init__(self, home, running):
        self.home, self.running = home, running
        self.kills = 0

    def create_time(self):
        return 1001.0

    def environ(self):
        return {"HERMES_HOME": str(self.home), "PRIVATE_SENTINEL": "must-not-leak"}

    def exe(self):
        return "private-executable"

    def cwd(self):
        return "private-directory"

    def cmdline(self):
        return ["private-argument"]

    def kill(self):
        self.kills += 1

    def is_running(self):
        return self.running


@pytest.mark.parametrize("running,settlement", [(True, "pending"), (False, "identity_not_running")])
def test_cleanup_receipt_does_not_call_a_kill_request_terminal(tmp_path, monkeypatch, running, settlement):
    machine = Machine(root=tmp_path / "machine", profile_name="synthetic", base_url="", started=900.0)
    proc = OwnedProcess(machine.hermes_home, running)
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([proc]))

    machine.kill_owned()

    assert proc.kills == 1
    path = machine.logs / "process-cleanup.jsonl"
    assert path.is_file(), "cleanup must retain its requested-kill and exit observations"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    sample = next(row for row in rows if row["phase"] == "terminal_sample")
    assert sample["pid"] == 431
    assert sample["recorded_birth"] == 1001.0
    assert sample["identity_source"] == "owned_scan"
    assert sample["settlement"] == settlement
    assert "private-" not in path.read_text()
    assert "must-not-leak" not in path.read_text()


def test_ownership_errors_are_counted_without_recording_unrelated_processes(tmp_path, monkeypatch):
    machine = Machine(root=tmp_path / "machine", profile_name="synthetic", base_url="", started=900.0)
    proc = OwnedProcess(machine.hermes_home, True)

    class Unclassified(OwnedProcess):
        pid = 987

        def environ(self):
            raise psutil.AccessDenied(self.pid, name="private-host-process")

    monkeypatch.setattr(psutil, "process_iter", lambda: iter([Unclassified(None, True), proc]))

    machine.kill_owned()

    path = machine.logs / "process-cleanup.jsonl"
    assert path.is_file()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    scan = next(row for row in rows if row["phase"] == "cleanup_scan")
    assert scan["unclassified"] == {"AccessDenied": 1}
    assert all(row.get("pid") != 987 for row in rows)
    assert "private-host-process" not in path.read_text()


def test_failed_kill_is_not_rewritten_as_success_or_terminal(tmp_path, monkeypatch):
    machine = Machine(root=tmp_path / "machine", profile_name="synthetic", base_url="", started=900.0)

    class Refused(OwnedProcess):
        def kill(self):
            self.kills += 1
            raise psutil.AccessDenied(self.pid, name="private-sentinel")

    proc = Refused(machine.hermes_home, True)
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([proc]))

    machine.kill_owned()

    assert proc.kills == 1
    path = machine.logs / "process-cleanup.jsonl"
    assert path.is_file()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    failed = next(row for row in rows if row["phase"] == "kill_error")
    assert failed["error_type"] == "AccessDenied"
    assert not any(row["phase"] == "kill_returned" for row in rows)
    assert "private-sentinel" not in path.read_text()


def test_pid_reuse_is_reported_as_original_identity_gone_without_killing_again(tmp_path, monkeypatch):
    machine = Machine(root=tmp_path / "machine", profile_name="synthetic", base_url="", started=900.0)

    class Reused(OwnedProcess):
        _pid_reused = False

        def is_running(self):
            self._pid_reused = True
            return False

    proc = Reused(machine.hermes_home, False)
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([proc]))
    machine.kill_owned()
    rows = [json.loads(line) for line in (machine.logs / "process-cleanup.jsonl").read_text().splitlines()]
    sample = next(row for row in rows if row["phase"] == "terminal_sample")
    assert sample["settlement"] == "identity_not_running"
    assert sample["pid_reused"] is True
    assert sample["recorded_birth"] == 1001.0
    assert proc.kills == 1


def test_process_export_drops_unexpected_fields(tmp_path):
    from tests.e2e.core.windows_update._process_evidence import append_event
    path = tmp_path / "evidence.jsonl"
    append_event(path, {"phase": "kill_requested", "pid": 431,
                        "recorded_birth": 1001.0, "identity_source": "owned_scan",
                        "argv": "private-sentinel", "environment": "private-sentinel"})
    row = json.loads(path.read_text())
    assert row["pid"] == 431
    assert "private-sentinel" not in path.read_text()


def test_failed_diagnostic_storage_preserves_original_cleanup(tmp_path, monkeypatch):
    machine = Machine(root=tmp_path / "machine", profile_name="synthetic", base_url="", started=900.0)
    machine.root.mkdir()
    (machine.root / "transcripts").write_text("not a directory")
    proc = OwnedProcess(machine.hermes_home, True)
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([proc]))
    machine.kill_owned()
    assert proc.kills == 1


def test_cleanup_capture_limit_does_not_limit_kills(tmp_path, monkeypatch):
    machine = Machine(root=tmp_path / "machine", profile_name="synthetic", base_url="", started=900.0)
    procs = [OwnedProcess(machine.hermes_home, True) for _ in range(70)]
    for i, proc in enumerate(procs):
        proc.pid = 10000 + i
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(procs))
    machine.kill_owned()
    assert all(proc.kills == 1 for proc in procs)
    rows = [json.loads(line) for line in (machine.logs / "process-cleanup.jsonl").read_text().splitlines()]
    assert any(row["phase"] == "cleanup_capture_truncated" for row in rows)


def test_cleanup_issues_all_kills_before_exit_samples_and_receipt_io(tmp_path, monkeypatch):
    from tests.e2e.core.windows_update import _process_evidence
    machine = Machine(root=tmp_path / "machine", profile_name="synthetic", base_url="", started=900.0)
    order = []

    class Traced(OwnedProcess):
        def kill(self):
            order.append(("kill", self.pid))
            super().kill()

        def is_running(self):
            order.append(("sample", self.pid))
            return True

    procs = [Traced(machine.hermes_home, True), Traced(machine.hermes_home, True)]
    procs[1].pid = 432
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(procs))
    monkeypatch.setattr(_process_evidence, "append_event", lambda *a: order.append(("write", None)))
    machine.kill_owned()
    assert order[:2] == [("kill", 431), ("kill", 432)]


def test_crash_root_kill_is_not_delayed_by_diagnostic_receipt_io(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from tests.e2e.core.windows_update import _process_evidence, test_crash_cells as crash
    order = []

    class Root:
        pid = 741
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            order.append("wait")
            self.returncode = -9
            return -9

    def kill(pid):
        order.append("kill")
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(crash, "taskkill_tree", kill)
    monkeypatch.setattr(_process_evidence, "append_event", lambda *a: order.append("write"))
    machine = SimpleNamespace(logs=tmp_path, kill_owned=lambda: None)
    assert crash._kill_when(machine, Root(), "handoff", lambda *a: "update child", "target") == "update child"
    assert order[:2] == ["kill", "wait"]


def test_root_kill_receipt_does_not_claim_a_birth_bound_kill(tmp_path):
    from types import SimpleNamespace
    from tests.e2e.core.windows_update._process_evidence import record_root, root_observation
    machine = SimpleNamespace(logs=tmp_path)
    proc = SimpleNamespace(pid=741)
    request = root_observation(proc, "root_kill_requested")
    record_root(machine, proc, "root_wait_returned", returncode=-9, request=request)
    rows = [json.loads(line) for line in (tmp_path / "process-cleanup.jsonl").read_text().splitlines()]
    assert rows[0]["operation_binding"] == "pid"
    assert rows[1]["operation_binding"] == "existing_wait"
    assert all(row["recorded_birth"] is None and row["birth_state"] == "not_observed" for row in rows)


def test_file_limit_has_a_terminal_truncation_record_without_changing_kills(tmp_path, monkeypatch):
    from tests.e2e.core.windows_update import _process_evidence
    monkeypatch.setattr(_process_evidence, "_MAX_BYTES", 1024)
    machine = Machine(root=tmp_path / "machine", profile_name="synthetic", base_url="", started=900.0)
    proc = OwnedProcess(machine.hermes_home, True)
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([proc]))
    for _ in range(20):
        machine.kill_owned()
    path = machine.logs / "process-cleanup.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[-1]["phase"] == "cleanup_file_truncated"
    assert rows[-1]["byte_limit"] == 1024
    assert path.stat().st_size <= 1024
    assert proc.kills == 20


def test_receipt_byte_cap_is_independent_of_text_stream_newline_defaults(tmp_path, monkeypatch):
    """Exercise a translating stream as data; do not pretend the interpreter is on another OS."""
    from tests.e2e.core.windows_update import _process_evidence
    monkeypatch.setattr(_process_evidence, "_MAX_BYTES", 1024)
    path = tmp_path / "translated.jsonl"

    class TranslatedTextPath:
        def __getattr__(self, name):
            return getattr(path, name)

        def open(self, *args, **kwargs):
            kwargs.setdefault("newline", "\r\n")
            return path.open(*args, **kwargs)

    for _ in range(20):
        _process_evidence.append_event(TranslatedTextPath(), {"phase": "kill_requested", "pid": 431})
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[-1]["phase"] == "cleanup_file_truncated"
    assert path.stat().st_size <= 1024
