"""Bounded loopback HTTP I/O for the source-only request controller."""

from __future__ import annotations

from contextlib import suppress
import http.client
import socket
import threading
import time


class DeadlineConnection(http.client.HTTPConnection):
    """An absolute watchdog interrupts headers/body reads, including trickled bodies.

    Keep the transport reference: HTTPConnection may relinquish ``sock`` while a
    close-delimited response still owns its file. Abort intent also survives a
    connection being created after the watchdog fires.
    """

    def __init__(self, host: str, port: int, deadline: float):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTP deadline expired")
        super().__init__(host, port, timeout=remaining)
        self._deadline = deadline
        self._aborted = threading.Event()
        self._transport_lock = threading.Lock()
        self._transport = None
        self._watchdog = threading.Timer(remaining, self.abort)
        self._watchdog.daemon = True
        self._watchdog.start()

    def connect(self) -> None:
        if self._aborted.is_set():
            raise TimeoutError("HTTP operation aborted")
        super().connect()
        with self._transport_lock:
            self._transport = self.sock
        if self._aborted.is_set() or time.monotonic() >= self._deadline:
            self.abort()
            raise TimeoutError("HTTP deadline expired or cancelled during connect")

    def abort(self) -> None:
        self._aborted.set()
        with self._transport_lock:
            transport = self._transport
        if transport is not None:
            with suppress(OSError):
                transport.shutdown(socket.SHUT_RDWR)

    def finish(self) -> None:
        self._watchdog.cancel()
        self.abort()
        self.close()


def read_response(conn: DeadlineConnection, *, limit: int) -> tuple[int, bytes]:
    response = conn.getresponse()
    try:
        if response.length is None and not response.chunked:
            raise http.client.HTTPException(
                "unframed response cannot establish completion"
            )
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError("HTTP response exceeds the bounded body size")
        # Sized HTTPResponse.read does not raise on truncated Content-Length.
        if not response.chunked and response.length != 0:
            raise http.client.IncompleteRead(body)
        if time.monotonic() >= conn._deadline:
            raise TimeoutError("HTTP response arrived after deadline")
        return response.status, body
    finally:
        response.close()
