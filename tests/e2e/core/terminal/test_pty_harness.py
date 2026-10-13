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
