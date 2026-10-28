"""Keep stalled real-process tests diagnostic without relaxing their deadlines."""

from types import SimpleNamespace

import pytest

from tests.e2e.core.windows import _rpc


def _child_argv(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(_rpc.subprocess, "Popen", lambda argv, **kwargs: captured.append(argv))
    monkeypatch.setattr(_rpc.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    _rpc.StdioGateway(SimpleNamespace(project=tmp_path, env=dict))
    return captured[0]


@pytest.mark.parametrize("failed", [False, True])
def test_child_stack_timer_surrounds_the_real_entry_module(monkeypatch, tmp_path, failed):
    import faulthandler
    import runpy
    import sys

    argv = _child_argv(monkeypatch, tmp_path)
    assert argv[1] == "-c", "the test child lacks a pre-timeout traceback timer"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", ["", *sys.path[1:]])
    events = []
    monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda seconds: events.append(("arm", seconds)))
    monkeypatch.setattr(faulthandler, "cancel_dump_traceback_later", lambda: events.append("cancel"))

    def entry(module, **kwargs):
        assert sys.path[0] == str(tmp_path.resolve())
        events.append((module, kwargs))
        if failed:
            raise RuntimeError("synthetic-entry-error")

    monkeypatch.setattr(runpy, "run_module", entry)
    if failed:
        with pytest.raises(RuntimeError, match="synthetic-entry-error"):
            exec(argv[2], {})
    else:
        exec(argv[2], {})
    assert events == [
        ("arm", 120),
        ("tui_gateway.entry", {"run_name": "__main__", "alter_sys": True}),
        "cancel",
    ]


def test_timeout_identifies_the_slow_build_without_dumping_other_payloads(monkeypatch):
    gateway = _rpc.StdioGateway.__new__(_rpc.StdioGateway)
    gateway.events = [
        {"type": "notification.show", "payload": {"key": "agent-build-slow", "level": "info"}},
        {"type": "message.delta", "payload": {"text": "unrelated-prompt-sentinel"}},
    ]
    gateway._stderr = [b"stack-sentinel\n"]
    gateway._request_count = lambda: 0
    times = iter([0, 1])
    monkeypatch.setattr(_rpc.time, "monotonic", lambda: next(times))
    with pytest.raises(AssertionError) as error:
        gateway.wait_event("message.complete", timeout=0)
    message = str(error.value)
    assert "agent-build-slow" in message
    assert "fake_provider_requests=0" in message
    assert "stack-sentinel" in message
    assert "unrelated-prompt-sentinel" not in message
