"""Offline transport and dispatch races; all endpoints/process identities are synthetic."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest

from hermes_cli.local_runtime import request_control as rc
from hermes_cli.local_runtime.request_io import DeadlineConnection


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
