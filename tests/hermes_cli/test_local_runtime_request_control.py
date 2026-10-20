"""Request-scoped cancellation against a loopback fake of the router child's HTTP surface.

No model, engine or live endpoint: the fake speaks only the routes the probe reads, so the real
socket-close path and the verification races are exercised offline.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import select
import socket
import threading

import pytest

from hermes_cli.local_runtime.request_control import (
    CancelOutcome,
    ManagedChildProbe,
    RequestRefused,
    ScopedRequest,
)
from tests.waiting import wait_until

MODEL = "fake-model"


class _Fake:
    """Shared state the handler reads; every flag is flipped by the test."""

    def __init__(self):
        self.resident = True
        self.honor_disconnect = True
        self.fail_slots = False
        self.omit_metrics = False
        self.foreign_busy = False
        self.deferred = 0
        self.busy_task: int | None = None
        self.next_task = 100
        self.release = threading.Event()
        self.posts = 0
        self.lock = threading.Lock()


def _handler(fake: _Fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _send(self, code, body, ctype="application/json"):
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # disconnect is the behavior under test

        def do_GET(self):
            route = self.path.split("?")[0]
            if route == "/models":
                value = "loaded" if fake.resident else "unloaded"
                return self._send(
                    200, {"data": [{"id": MODEL, "status": {"value": value}}]}
                )
            if not fake.resident:
                return self._send(400, {"error": "model is not loaded"})
            if route == "/slots":
                if fake.fail_slots:
                    return self._send(500, {"error": "boom"})
                rows = [
                    {
                        "id": 0,
                        "is_processing": fake.busy_task is not None,
                        "id_task": fake.busy_task if fake.busy_task is not None else -1,
                    }
                ]
                if fake.foreign_busy:
                    rows.append({"id": 1, "is_processing": True, "id_task": 999})
                return self._send(200, rows)
            if route == "/metrics":
                busy = (fake.busy_task is not None) + fake.foreign_busy
                text = (
                    ""
                    if fake.omit_metrics
                    else (
                        f"llamacpp:requests_processing {busy}\nllamacpp:requests_deferred "
                        f"{fake.deferred}\n"
                    )
                )
                return self._send(200, text.encode(), "text/plain")
            return self._send(404, {})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            if self.path == "/models/unload":
                fake.resident = False
                fake.busy_task = None
                return self._send(200, {})
            with fake.lock:
                fake.posts += 1
                fake.busy_task = fake.next_task
                fake.next_task += 1
            while True:
                if fake.release.wait(0.01):
                    fake.busy_task = None
                    return self._send(
                        200, {"choices": [{"message": {"content": "ok"}}]}
                    )
                if not fake.resident:
                    return
                if fake.honor_disconnect:
                    ready, _, _ = select.select([self.connection], [], [], 0)
                    if ready and self.connection.recv(1, socket.MSG_PEEK) == b"":
                        fake.busy_task = None
                        return

    return Handler


@pytest.fixture
def fake():
    state = _Fake()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    state.release.set()
    server.shutdown()
    server.server_close()


def _request(fake: _Fake) -> ScopedRequest:
    probe = ManagedChildProbe(fake.base, "key", MODEL, timeout_s=2)
    return ScopedRequest(probe, {"model": MODEL, "messages": []}, request_timeout_s=20)


def test_closed_transport_and_idle_child_are_observation_not_native_proof(fake):
    req = _request(fake)
    assert req.start() == "observed"
    assert req.bound_task == 100
    result = req.cancel(timeout_s=10, interval_s=0.02)
    assert result.outcome is CancelOutcome.IDLE_AFTER_CLOSE
    assert not result.cancelled and result.idle_reads >= 2
    assert fake.busy_task is None
    assert req.wait(0) is None  # the client never saw a response


def test_cancel_after_completion_reports_completion_not_cancel(fake):
    req = _request(fake)
    req.start()
    fake.release.set()
    assert req.wait(10) is not None
    result = req.cancel(interval_s=0.02)
    assert result.outcome is CancelOutcome.COMPLETED_BEFORE_CANCEL
    assert result.completed and not result.cancelled


def test_server_that_ignores_disconnect_is_not_confirmed(fake):
    fake.honor_disconnect = False
    req = _request(fake)
    req.start()
    result = req.cancel(timeout_s=0.6, interval_s=0.02)
    assert result.outcome is CancelOutcome.NOT_CONFIRMED
    assert not result.cancelled and "still busy" in result.detail


def test_generic_request_cannot_evict_even_after_busy_cancel(fake):
    fake.honor_disconnect = False
    req = _request(fake)
    req.start()
    assert (
        req.cancel(timeout_s=0.3, interval_s=0.02).outcome
        is CancelOutcome.NOT_CONFIRMED
    )
    result = req.evict(timeout_s=5, interval_s=0.02)
    assert result.outcome is CancelOutcome.UNVERIFIABLE and not result.cancelled
    assert fake.resident is True


def test_unreadable_telemetry_never_claims_cancel(fake):
    req = _request(fake)
    req.start()
    fake.fail_slots = True
    result = req.cancel(timeout_s=0.5, interval_s=0.02)
    assert result.outcome is CancelOutcome.UNVERIFIABLE
    assert not result.cancelled and "telemetry unreadable" in result.detail


def test_missing_metrics_line_is_unverifiable_not_idle(fake):
    req = _request(fake)
    req.start()
    fake.omit_metrics = True
    result = req.cancel(timeout_s=0.5, interval_s=0.02)
    assert result.outcome is CancelOutcome.UNVERIFIABLE and not result.cancelled


def test_deferred_queue_blocks_confirmation(fake):
    req = _request(fake)
    req.start()
    fake.deferred = 1
    result = req.cancel(timeout_s=0.5, interval_s=0.02)
    assert result.outcome is CancelOutcome.NOT_CONFIRMED and not result.cancelled


def test_busy_child_refuses_to_send(fake):
    fake.foreign_busy = True
    with pytest.raises(RequestRefused):
        _request(fake).start()
    assert fake.posts == 0


def test_unreadable_telemetry_refuses_to_send(fake):
    fake.fail_slots = True
    with pytest.raises(RequestRefused):
        _request(fake).start()
    assert fake.posts == 0


def test_foreign_request_makes_binding_ambiguous_and_blocks_cancel_claim(fake):
    req = _request(fake)
    probe_state = req._probe
    t = threading.Thread(
        target=lambda: (
            wait_until(lambda: fake.posts) and setattr(fake, "foreign_busy", True)
        )
    )
    t.start()
    assert req.start() in ("ambiguous", "observed")
    t.join()
    assert probe_state.read().busy_slots  # sanity: server shows work
    result = req.cancel(timeout_s=0.5, interval_s=0.02)
    assert not result.cancelled  # the foreign task keeps the server busy


def test_model_leaving_during_verification_is_not_an_ack(fake):
    req = _request(fake)
    req.start()
    fake.resident = False
    result = req.cancel(timeout_s=0.5, interval_s=0.02)
    assert result.outcome is CancelOutcome.MODEL_NOT_RESIDENT and not result.cancelled


def test_cancel_is_idempotent_and_safe_from_two_threads(fake):
    req = _request(fake)
    req.start()
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(req.cancel(interval_s=0.02)))
        for _ in range(2)
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 2 and results[0].outcome is results[1].outcome
    assert req.cancel() is results[0] or req.cancel().outcome is results[0].outcome


@pytest.mark.parametrize("round_", range(15))
def test_completion_racing_cancel_never_reports_a_cancelled_completed_request(
    fake, round_
):
    req = _request(fake)
    req.start()
    racer = threading.Thread(target=fake.release.set)
    racer.start()
    result = req.cancel(timeout_s=10, interval_s=0.01)
    racer.join()
    if req.wait(0) is not None:
        assert not result.cancelled
    else:
        assert result.outcome in (
            CancelOutcome.IDLE_AFTER_CLOSE,
            CancelOutcome.COMPLETED_BEFORE_CANCEL,
        )
    if result.cancelled:
        assert req.wait(0) is None


def test_non_loopback_endpoint_is_rejected():
    with pytest.raises(ValueError):
        ManagedChildProbe("http://192.0.2.10:18434", "key", MODEL)
    with pytest.raises(ValueError):
        ManagedChildProbe("https://127.0.0.1:18434", "key", MODEL)


def test_request_is_single_use_even_before_cancellation(fake):
    request = _request(fake)
    request.start()
    try:
        with pytest.raises(RequestRefused):
            request.start()
        assert fake.posts == 1
    finally:
        request.cancel(timeout_s=1, interval_s=0.01)


def test_unproven_cancel_quarantines_cooperating_requests(fake):
    request = _request(fake)
    request.start()
    result = request.cancel(timeout_s=1, interval_s=0.01)
    assert result.outcome is CancelOutcome.IDLE_AFTER_CLOSE
    assert not result.cancelled and fake.busy_task is None
    with pytest.raises(RequestRefused):
        _request(fake).start()
    assert fake.posts == 1
