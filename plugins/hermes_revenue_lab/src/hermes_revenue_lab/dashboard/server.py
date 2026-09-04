"""Loopback-only read-only HTTP server for HRL-16."""

from __future__ import annotations

import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .render import render_dashboard
from .types import DashboardSnapshot

SnapshotProvider = Callable[[], DashboardSnapshot]


def dashboard_server(
    provider: SnapshotProvider,
    *,
    host: str = "127.0.0.1",
    port: int = 9131,
) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("revenue dashboard must bind only to 127.0.0.1")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("revenue dashboard port is invalid")

    class Handler(BaseHTTPRequestHandler):
        def _headers(self, status: int, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
            )
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/health":
                payload = json.dumps({"status": "ok", "mode": "read_only"}).encode()
                self._headers(200, "application/json; charset=utf-8", len(payload))
                self.wfile.write(payload)
                return
            if self.path not in {"/", "/api/snapshot"}:
                payload = b'{"error":"not_found"}'
                self._headers(404, "application/json; charset=utf-8", len(payload))
                self.wfile.write(payload)
                return
            try:
                snapshot = provider()
                if self.path == "/":
                    payload = render_dashboard(snapshot).encode()
                    content_type = "text/html; charset=utf-8"
                else:
                    payload = json.dumps(
                        snapshot.canonical_record(), sort_keys=True
                    ).encode()
                    content_type = "application/json; charset=utf-8"
            except (OSError, TypeError, ValueError):
                payload = b'{"error":"snapshot_unavailable"}'
                self._headers(503, "application/json; charset=utf-8", len(payload))
                self.wfile.write(payload)
                return
            self._headers(200, content_type, len(payload))
            self.wfile.write(payload)

        def do_POST(self) -> None:
            payload = b'{"error":"read_only"}'
            self._headers(405, "application/json; charset=utf-8", len(payload))
            self.wfile.write(payload)

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
