"""Test PTY readiness diagnostics without opening a PTY or launching Hermes."""

import threading
from types import SimpleNamespace

import pytest


@pytest.mark.platforms("posix")
def test_raw_mode_timeout_retains_screen_and_bounded_bytes(monkeypatch):
    from tests.e2e.core.terminal import _pty

    term = object.__new__(_pty.PtyHermes)
    term.proc = SimpleNamespace(poll=lambda: None)
    term._lock = threading.Lock()
    term.raw = bytearray(b"omitted-prefix" + b"x" * 600 + b"startup-tail\x1b[0m")
    term.dump = lambda: "startup screen marker"
    term._raw_mode = lambda: False
    monkeypatch.setattr(_pty, "time", SimpleNamespace(monotonic=lambda: 100.0))

    def timeout(fn, *, timeout, what):
        assert fn() is False
        assert timeout == 120.0
        raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")

    monkeypatch.setattr(_pty, "poll", timeout)

    with pytest.raises(AssertionError) as excinfo:
        term.wait_ready()

    message = str(excinfo.value)
    assert "timed out after 120s waiting for the UI to take the terminal (raw mode)" in message
    assert "startup screen marker" in message
    assert repr(bytes(term.raw[-600:])) in message
    assert "omitted-prefix" not in message


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("case", [
    "owned", "owner_group_differs", "dead_owner", "reused_owner", "reused_supervisor",
    "untracked_owner", "untracked_supervisor", "unrelated_owner",
    "wrong_script", "wrong_group", "not_new_session",
    "owner_exits_during_check", "supervisor_exits_during_check",
])
def test_gateway_supervisor_exemption_requires_live_tracked_identities(monkeypatch, case):
    from tests.e2e.core.terminal import _pty

    stat = {100: (1, 100, 100, 11), 201: (100, 201, 201, 21)}
    stat.update({
        "owner_group_differs": {100: (1, 88, 88, 11)},
        "dead_owner": {100: None},
        "reused_owner": {100: (1, 100, 100, 12)},
        "reused_supervisor": {201: (100, 201, 201, 22)},
        "not_new_session": {201: (100, 100, 100, 21)},
    }.get(case, {}))
    seen = {(100, 11), (201, 21)}
    seen.difference_update({
        "untracked_owner": {(100, 11)},
        "untracked_supervisor": {(201, 21)},
    }.get(case, set()))
    gateways = set() if case == "unrelated_owner" else {100}
    commands = {100: "gateway"}
    script = str(_pty.REPO_ROOT / "hermes_cli" / "process_supervisor.sh")
    argv = [
        "/bin/sh",
        "/tmp/process_supervisor.sh" if case == "wrong_script" else script,
        {"owner_group_differs": "88", "wrong_group": "999"}.get(case, "100"),
    ]
    exit_on_second = {
        "owner_exits_during_check": 100,
        "supervisor_exits_during_check": 201,
    }.get(case)

    calls = {100: 0, 201: 0}

    def live_stat(pid):
        calls[pid] += 1
        if pid == exit_on_second and calls[pid] > 1:
            return None
        return stat[pid]

    monkeypatch.setattr(_pty, "_live_process_stat", live_stat)
    monkeypatch.setattr(_pty, "_process_argv", lambda _pid: list(argv))
    monkeypatch.setattr(_pty, "cmdline", lambda pid: commands.get(pid, "supervisor"))

    assert _pty._gateway_owned_supervisor(
        201, gateways, seen, lambda command: command == "gateway",
    ) is (case in {"owned", "owner_group_differs"})


@pytest.mark.platforms("posix")
def test_leftover_processes_exempts_only_verified_gateway_supervisor(monkeypatch):
    from tests.e2e.core.terminal import _pty

    term = object.__new__(_pty.PtyHermes)
    term.sid = 40
    term.seen_members = {(100, 11), (201, 21)}
    stat = {100: (1, 100, 100, 11), 201: (100, 201, 201, 21)}
    command = {
        100: "python -m hermes_cli.main --profile default gateway run --quiet --idle-exit",
        201: f"/bin/sh {_pty.REPO_ROOT / 'hermes_cli' / 'process_supervisor.sh'} 100",
    }
    monkeypatch.setattr(_pty, "session_members", lambda _sid: [])
    monkeypatch.setattr(_pty, "_start_time", lambda pid: stat[pid][3])
    monkeypatch.setattr(_pty, "_live_process_stat", lambda pid: stat[pid])
    monkeypatch.setattr(_pty, "_process_argv", lambda _pid: [
        "/bin/sh", str(_pty.REPO_ROOT / "hermes_cli" / "process_supervisor.sh"), "100",
    ])
    monkeypatch.setattr(_pty, "cmdline", lambda pid: command[pid])

    def check_poll(fn, *, timeout, what):
        assert timeout == 0.01
        assert fn() is True
        return True

    monkeypatch.setattr(_pty, "poll", check_poll)
    assert term.leftover_processes(timeout=0.01) == []


@pytest.mark.platforms("posix")
def test_leftover_processes_still_reports_supervisor_without_live_owner(monkeypatch):
    from tests.e2e.core.terminal import _pty

    term = object.__new__(_pty.PtyHermes)
    term.sid = 40
    term.seen_members = {(201, 21)}
    monkeypatch.setattr(_pty, "session_members", lambda _sid: [])
    monkeypatch.setattr(_pty, "_start_time", lambda pid: 21 if pid == 201 else None)
    monkeypatch.setattr(_pty, "_live_process_stat", lambda pid: (100, 201, 201, 21) if pid == 201 else None)
    monkeypatch.setattr(_pty, "_process_argv", lambda _pid: [
        "/bin/sh", str(_pty.REPO_ROOT / "hermes_cli" / "process_supervisor.sh"), "100",
    ])
    monkeypatch.setattr(_pty, "cmdline", lambda _pid: "/bin/sh process_supervisor.sh 100")
    monkeypatch.setattr(_pty, "_process_identity", lambda pid: f"pid={pid}")

    def check_poll(fn, *, timeout, what):
        assert fn() is False
        raise AssertionError("still alive")

    monkeypatch.setattr(_pty, "poll", check_poll)
    leftovers = term.leftover_processes(timeout=0.01)
    assert len(leftovers) == 1
    assert "pid=201" in leftovers[0]
