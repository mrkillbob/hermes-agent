"""A stale monitor must not abandon a retry that started after its sampled attempt."""
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest

from agent.chat_completion_helpers import _StreamingCall


def _call():
    call = _StreamingCall.__new__(_StreamingCall)
    call.stream_attempt_lock = Lock()
    call.stream_attempt_state = {"current": 1, "cancelled": {1}}
    call.last_chunk_time = {"t": 0.0}
    call._last_killed_attempt, call._same_attempt_kills = 1, 2
    call.worker, call.api_kwargs = object(), {}
    call.agent = SimpleNamespace(_interrupt_requested=False)
    call._request_cancelled = {"value": False}
    call._call_done = Event()
    call.provider_tool_in_flight, call.prompt_progress = {"yes": False}, {"progress": None}
    call.result, call.deltas_were_sent = {"partial_tool_names": []}, {"yes": False}
    return call


def test_retry_started_before_terminal_abandonment_keeps_its_request(monkeypatch):
    call = _call()
    entered, proceed = Event(), Event()
    abandon = call._abandon_wedged_worker

    def pause_terminal_abandonment(elapsed, attempt):
        entered.set()
        assert proceed.wait(10)
        abandon(elapsed, attempt)

    monkeypatch.setattr(call, "_abandon_wedged_worker", pause_terminal_abandonment)
    monitor = Thread(target=lambda: call._rearm_or_abandon_after_kill(1, expected_attempt_id=1))
    monitor.start()
    try:
        assert entered.wait(10)
        retry = call._start_stream_attempt()
        assert retry == 2 and call._stream_attempt_is_active(retry)
    finally:
        proceed.set()
        monitor.join(10)
    assert not monitor.is_alive()
    assert call._stream_attempt_is_active(retry)
    assert not call._request_cancelled["value"]
    assert call._abandon_error is None
    assert not call._call_done.is_set()


def test_terminal_abandonment_prevents_a_late_retry_from_dispatching():
    call = _call()
    call._abandon_wedged_worker(1, 1)
    with pytest.raises(InterruptedError, match="before retry"):
        call._start_stream_attempt()
    assert call.stream_attempt_state["current"] == 1
    assert isinstance(call._abandon_error, TimeoutError)
    assert call._request_cancelled["value"] and call._call_done.is_set()
