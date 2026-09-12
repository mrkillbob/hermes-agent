"""Small persistent loopback broker for the inter-agent tool.

The broker is deliberately a separate process so several Hermes conversations can
share one queue. It binds only to loopback and stores messages in the active
Hermes state home.
"""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from hermes_constants import get_hermes_home

HOST = "127.0.0.1"
PORT = 8765


def _database() -> sqlite3.Connection:
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(home / "inter-agent-messages.db", timeout=10)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "sender TEXT NOT NULL, recipient TEXT NOT NULL, body TEXT NOT NULL,"
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.commit()
    return connection


class _Handler(BaseHTTPRequestHandler):
    def _write(self, status: int, payload: object) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write(200, {"ok": True})
            return
        if parsed.path == "/receive":
            query = parse_qs(parsed.query)
            recipient = query.get("to", [""])[0]
            since = int(query.get("since", [0])[0])
            with _database() as db:
                rows = db.execute(
                    "SELECT id, sender, recipient, body, created_at FROM messages "
                    "WHERE recipient = ? AND id > ? ORDER BY id",
                    (recipient, since),
                ).fetchall()
            self._write(200, [
                {"id": row[0], "from": row[1], "to": row[2], "body": row[3], "created_at": row[4]}
                for row in rows
            ])
            return
        if parsed.path == "/history":
            query = parse_qs(parsed.query)
            peer = query.get("with_", [""])[0]
            with _database() as db:
                if peer:
                    rows = db.execute(
                        "SELECT id, sender, recipient, body, created_at FROM messages "
                        "WHERE sender = ? OR recipient = ? ORDER BY id",
                        (peer, peer),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT id, sender, recipient, body, created_at FROM messages ORDER BY id"
                    ).fetchall()
            self._write(200, [
                {"id": row[0], "from": row[1], "to": row[2], "body": row[3], "created_at": row[4]}
                for row in rows
            ])
            return
        self._write(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/send":
            self._write(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            sender = str(payload["from"])
            recipient = str(payload["to"])
            body = str(payload["body"])
            if not sender or not recipient or not body:
                raise ValueError("from, to, and body are required")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._write(400, {"error": str(exc)})
            return
        with _database() as db:
            cursor = db.execute(
                "INSERT INTO messages(sender, recipient, body) VALUES (?, ?, ?)",
                (sender, recipient, body),
            )
            message_id = cursor.lastrowid
            db.commit()
        self._write(200, {"id": message_id})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), _Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
