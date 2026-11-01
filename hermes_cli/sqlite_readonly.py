"""Normal SQLite locking and snapshots for inspection without schema initialization."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time

from hermes_cli.sqlite_safe_read import connect_tracked


@contextmanager
def readonly_snapshot(path: Path):
    """Read an existing database, including committed WAL frames, in one snapshot.

    mode=ro prevents database writes, not SQLite WAL/SHM bookkeeping. A supported
    SQLite build may create sidecars when the directory permits it. Missing or
    inaccessible sidecars remain an ordinary SQLite error: never fall back to
    immutable/nolock, raw copies, repair, or an initializing connection.
    No running owner or application quiescence is required; SQLite owns locking.
    SQL work has a five-second progress deadline and an eight-MiB row/value cap.
    The deadline cannot interrupt a stalled OS call or arbitrary caller Python.
    """
    path = path.resolve()
    conn = connect_tracked(path.as_uri() + '?mode=ro', tracking_path=path,
                           uri=True, isolation_level=None, timeout=5.0)
    try:
        deadline = time.monotonic() + 5.0
        conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('BEGIN')
        # BEGIN is deferred. A real read pins the snapshot before the caller
        # queries multiple tables while other connections may commit.
        conn.execute('SELECT count(*) FROM sqlite_schema').fetchone()
        yield conn
    finally:
        # Closing rolls back any remaining read transaction, also on exceptions.
        conn.close()
