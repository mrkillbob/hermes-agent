"""Offline transport and dispatch races; all endpoints/process identities are synthetic."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from types import SimpleNamespace

import pytest

from hermes_cli.local_runtime import request_control as rc
from hermes_cli.local_runtime.request_io import DeadlineConnection


def test_loading_time_and_delayed_worker_cannot_extend_original_deadline(monkeypatch):
    clock = [90.0]
    posts = []
    reads = []
    monkeypatch.setattr(rc, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    class DeferredWorker:
        def start(self):
            pass

    class Connection:
        def __init__(self, *args):
            pass

        def connect(self):
            pass

        def request(self, *args, **kwargs):
            posts.append(True)

        def finish(self):
            pass

    def read(**kwargs):
        reads.append(True)
        return rc.ChildState(True, () if len(reads) == 1 else ((0, 1),), 0, 0)

    monkeypatch.setattr(rc, 'spawn_context_thread', lambda *args, **kwargs: DeferredWorker())
    monkeypatch.setattr(rc, 'DeadlineConnection', Connection)
    monkeypatch.setattr(rc, 'read_response', lambda *args, **kwargs: (200, b'complete'))
    probe = SimpleNamespace(model_id='fixture', host='fixture', port=1, api_key='fixture', read=read)
    request = rc.ScopedRequest(probe, {'model': 'fixture'}, request_timeout_s=10, absolute_deadline=100)
    clock[0] = 99.9  # most of the original deadline was spent waiting for load
    request.start()
    clock[0] = 100.1  # the request worker was scheduled only after that deadline
    request._run()
    assert posts == []
    assert isinstance(request.error, rc.RequestRefused)
    assert request._finished.is_set()
    assert not request._lease


def test_deadline_abort_survives_late_socket_creation(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    transports = []

    class Transport:
        def __init__(self):
            self.stopped = False

        def shutdown(self, how):
            self.stopped = True

        def close(self):
            self.stopped = True

    def connect(self):
        entered.set()
        assert release.wait(2)
        self.sock = Transport()
        transports.append(self.sock)

    monkeypatch.setattr("http.client.HTTPConnection.connect", connect)
    conn = DeadlineConnection("127.0.0.1", 12345, time.monotonic() + 10)
    errors = []

    def worker():
        try:
            conn.connect()
        except TimeoutError as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(2)
        conn.abort()
        release.set()
        thread.join(2)
        assert not thread.is_alive() and errors and transports[0].stopped
    finally:
        release.set()
        thread.join(2)
        conn.finish()


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_absolute_deadline_interrupts_stalled_response(phase):
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if phase == "body":
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"x")
                self.wfile.flush()
            release.wait(3)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    probe = rc.ManagedChildProbe(
        f"http://127.0.0.1:{server.server_port}", "key", "model", timeout_s=10
    )
    started = time.monotonic()
    try:
        with pytest.raises(rc.ProbeError):
            probe._get("/models", deadline=started + 0.1)
        assert time.monotonic() - started < 2
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_cancel_intent_survives_connection_constructor_race(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    posts = []

    class Connection:
        def __init__(self, *args):
            entered.set()
            assert release.wait(2)

        def connect(self):
            pass

        def request(self, *args, **kwargs):
            posts.append(True)

        def abort(self):
            pass

        def finish(self):
            pass

    monkeypatch.setattr(rc, "DeadlineConnection", Connection)
    probe = rc.ManagedChildProbe("http://127.0.0.1:12345", "key", "target")
    monkeypatch.setattr(probe, "read", lambda **kw: rc.ChildState(True, (), 0.0, 0.0))
    request = rc.ScopedRequest(probe, {"model": "target"})
    # Start the actual worker with constructor paused before publication.
    request._request_deadline = time.monotonic() + 10
    request._state = "active"
    request._worker = threading.Thread(target=request._run)
    request._worker.start()
    try:
        assert entered.wait(2)
        result = request.cancel(timeout_s=0)
        assert not result.cancelled
        release.set()
        request._worker.join(2)
        assert not request._worker.is_alive() and posts == []
        with pytest.raises(rc.RequestRefused):
            request.start()
    finally:
        release.set()
        request._worker.join(2)


def test_truncated_content_length_is_not_a_completed_response():
    from io import BytesIO
    import http.client
    from types import SimpleNamespace
    from hermes_cli.local_runtime.request_io import read_response

    class Socket:
        def makefile(self, *args):
            return BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nx")

    response = http.client.HTTPResponse(Socket())
    response.begin()
    connection = SimpleNamespace(
        getresponse=lambda: response, _deadline=time.monotonic() + 10
    )
    with pytest.raises(http.client.HTTPException):
        read_response(connection, limit=1024)


def test_unframed_response_cannot_establish_completion_after_socket_close():
    from io import BytesIO
    import http.client
    from types import SimpleNamespace
    from hermes_cli.local_runtime.request_io import read_response

    class Socket:
        def makefile(self, *args):
            return BytesIO(b"HTTP/1.1 200 OK\r\n\r\nx")

    response = http.client.HTTPResponse(Socket())
    response.begin()
    connection = SimpleNamespace(
        getresponse=lambda: response, _deadline=time.monotonic() + 10
    )
    with pytest.raises(http.client.HTTPException):
        read_response(connection, limit=1024)


@pytest.mark.parametrize("phase", ["connect_failure", "cancel_before_send"])
def test_predispatch_exit_releases_lease_for_a_fresh_request(monkeypatch, phase):
    requests, posts = {}, []

    class Connection:
        def __init__(self, *args):
            pass

        def connect(self):
            if phase == "connect_failure":
                raise OSError("synthetic connect failure")
            requests["current"].cancel(timeout_s=0)

        def request(self, *args, **kwargs):
            posts.append(True)

        def abort(self):
            pass

        def finish(self):
            pass

    monkeypatch.setattr(rc, "DeadlineConnection", Connection)
    model = f"predispatch-{phase}"
    probe = rc.ManagedChildProbe("http://127.0.0.1:12414", "key", model)
    monkeypatch.setattr(probe, "read", lambda **kw: rc.ChildState(True, (), 0.0, 0.0))
    first = rc.ScopedRequest(probe, {"model": model})
    second = rc.ScopedRequest(probe, {"model": model})
    try:
        for request in (first, second):
            requests["current"] = request
            request.start(bind_timeout_s=1, interval_s=0.01)
            request._worker.join(2)
            assert not request._worker.is_alive()
        assert posts == []
    finally:
        first._release_lease()
        second._release_lease()


def test_cleanup_failure_still_publishes_predispatch_completion_and_releases_lease(
    monkeypatch,
):
    class Connection:
        def __init__(self, *args):
            pass

        def connect(self):
            raise OSError("synthetic connect failure")

        def finish(self):
            raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(rc, "DeadlineConnection", Connection)
    model = "predispatch-cleanup-error"
    probe = rc.ManagedChildProbe("http://127.0.0.1:12414", "key", model)
    monkeypatch.setattr(probe, "read", lambda **kw: rc.ChildState(True, (), 0.0, 0.0))
    first = rc.ScopedRequest(probe, {"model": model})
    second = rc.ScopedRequest(probe, {"model": model})
    try:
        first.start(bind_timeout_s=1, interval_s=0.01)
        first._worker.join(2)
        assert first._finished.is_set()
        second.start(bind_timeout_s=1, interval_s=0.01)
        second._worker.join(2)
        assert second._finished.is_set()
    finally:
        first._release_lease()
        second._release_lease()


def test_failed_send_remains_quarantined(monkeypatch):
    posts = []

    class Connection:
        def __init__(self, *args):
            pass

        def connect(self):
            pass

        def request(self, *args, **kwargs):
            posts.append(True)
            raise OSError("synthetic uncertain send failure")

        def finish(self):
            pass

    monkeypatch.setattr(rc, "DeadlineConnection", Connection)
    model = "uncertain-send"
    probe = rc.ManagedChildProbe("http://127.0.0.1:12414", "key", model)
    monkeypatch.setattr(probe, "read", lambda **kw: rc.ChildState(True, (), 0.0, 0.0))
    request = rc.ScopedRequest(probe, {"model": model})
    try:
        request.start(bind_timeout_s=1, interval_s=0.01)
        request._worker.join(2)
        assert request._finished.is_set() and len(posts) == 1
        with pytest.raises(rc.RequestRefused):
            rc.ScopedRequest(probe, {"model": model}).start()
    finally:
        request._release_lease()
