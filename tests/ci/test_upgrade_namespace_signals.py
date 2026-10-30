"""Census-to-signal exit races are not process ownership errors."""

import errno
import importlib
import signal

import pytest

from tests.e2e.core.upgrade.handoff import _ns_agent as agent


@pytest.mark.parametrize("sig", [signal.SIGTERM, 9])
def test_signal_to_process_that_exited_after_census_is_not_an_agent_error(
    monkeypatch, sig
):
    def exited(pid, requested):
        assert (pid, requested) == (123, sig)
        raise ProcessLookupError(errno.ESRCH, "process exited")

    monkeypatch.setattr(agent.os, "kill", exited)
    assert agent._kill({"pid": 123, "sig": sig}) == {"ok": False}


@pytest.mark.parametrize(
    "error", [PermissionError(errno.EPERM, "not owned"), OSError(errno.EIO, "I/O")]
)
def test_other_signal_errors_remain_protocol_errors(monkeypatch, error):
    def denied(*args):
        raise error

    monkeypatch.setattr(agent.os, "kill", denied)
    result = agent._kill({"pid": 123})
    assert result["ok"] is False and result["error"] == str(error)


def test_successful_signal_is_confirmed(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.os, "kill", lambda *args: calls.append(args))
    assert agent._kill({"pid": 123, "sig": signal.SIGTERM}) == {"ok": True}
    assert calls == [(123, signal.SIGTERM)]


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("module", ["test_hostile_post", "test_hostile_pause"])
@pytest.mark.parametrize("exit_signal", ["stop", "kill"])
def test_kill_census_reports_only_confirmed_kills(monkeypatch, exit_signal, module):
    post = importlib.import_module(f"tests.e2e.core.upgrade.git.{module}")

    class Host:
        def __init__(self):
            self.census = 0
            self.calls = []

        def procs(self):
            self.census += 1
            return (
                [{"pid": 101, "state": "S"}, {"pid": 102, "state": "S"}]
                if self.census == 1
                else []
            )

        def kill(self, pid, sig):
            self.calls.append((pid, sig))
            return not (pid == 101 and sig == exit_signal)

    exit_signal = {"stop": signal.SIGSTOP, "kill": signal.SIGKILL}[exit_signal]
    host = Host()
    monkeypatch.setattr(post.time, "sleep", lambda _: None)
    assert post._kill_born_after(host, set()) == [102]
    if exit_signal == signal.SIGSTOP:
        assert (101, signal.SIGKILL) not in host.calls


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("module", ["test_hostile_post", "test_hostile_pause"])
def test_census_does_not_hide_agent_ownership_error(monkeypatch, module):
    post = importlib.import_module(f"tests.e2e.core.upgrade.git.{module}")

    class Host:
        def procs(self):
            return [{"pid": 123, "state": "S"}]

        def kill(self, pid, sig):
            raise AssertionError("sandbox agent refused kill: permission denied")

    with pytest.raises(AssertionError, match="permission denied"):
        post._kill_born_after(Host(), set())
