"""Tests for the bounded kanban init lock (issue #36644).

`connect()` wrapped its entire body in an unbounded blocking `flock(LOCK_EX)`
on every call. A single process stalled inside the critical section blocked the
long-lived gateway dispatcher's next-tick `connect()` forever — no timeout, no
recovery, board silently stops being worked.

Two fixes, both covered here:
1. Fast path: once a path is initialized in this process, `connect()` skips the
   cross-process init lock entirely (nothing left to serialize), so a held lock
   cannot block a steady-state connect.
2. Bounded acquire: existing database initialization retains warning/fallback.
   Missing database creation refuses after the same deadline while reserved,
   then succeeds after release, without globally serializing initialized paths.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    return home


def _hold_init_lock(db_path: Path):
    """Return (start_event, release_event, thread) holding the init lock."""
    holding = threading.Event()
    release = threading.Event()

    def _holder():
        with kbc._cross_process_init_lock(db_path):
            holding.set()
            release.wait(timeout=10)

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    assert holding.wait(timeout=5), "holder thread never acquired the lock"
    return release, t


def test_initialized_path_connect_skips_init_lock(kanban_home):
    """A connect to an already-initialized path must not block on the init lock."""
    db_path = kb.kanban_db_path(board="default")
    # Initialize once.
    kbc.connect().close()
    assert str(db_path.resolve()) in kb._INITIALIZED_PATHS

    # Hold the init lock; a fast-path connect must return promptly anyway.
    release, t = _hold_init_lock(db_path)
    try:
        start = time.monotonic()
        kbc.connect().close()
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"fast-path connect blocked on the init lock ({elapsed:.2f}s)"
    finally:
        release.set()
        t.join(timeout=5)


def test_existing_database_init_is_bounded_when_lock_held(kanban_home, monkeypatch):
    """An existing uninitialized database keeps its bounded fallback contract."""
    monkeypatch.setattr(kbc, "_INIT_LOCK_TIMEOUT_SECONDS", 0.6)
    db_path = kb.kanban_db_path(board="default")
    db_path.touch()

    release, t = _hold_init_lock(db_path)
    try:
        start = time.monotonic()
        conn = kbc.connect()  # path NOT yet initialized — must take the bounded path
        conn.close()
        elapsed = time.monotonic() - start
        # Proceeded within roughly the timeout window (not unbounded).
        assert 0.4 <= elapsed < 8.0, f"expected bounded ~0.6s acquire, got {elapsed:.2f}s"
        assert str(db_path.resolve()) in kb._INITIALIZED_PATHS
    finally:
        release.set()
        t.join(timeout=5)


@pytest.mark.parametrize("cached", [False, True])
def test_missing_database_reservation_timeout_does_not_create_and_can_retry(
    kanban_home, monkeypatch, cached
):
    monkeypatch.setattr(kbc, "_INIT_LOCK_TIMEOUT_SECONDS", 0.6)
    db_path = kb.kanban_db_path(board="default")
    if cached:
        with kbc.connect_closing():
            pass
        db_path.unlink()
    release, thread = _hold_init_lock(db_path)
    try:
        with pytest.raises(TimeoutError, match="initialization reservation"):
            kbc.connect()
        assert not db_path.exists()
    finally:
        release.set()
        thread.join(5)
    with kbc.connect_closing() as conn:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
