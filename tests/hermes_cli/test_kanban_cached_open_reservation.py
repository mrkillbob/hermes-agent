"""Cached opens must not invalidate a reviewed missing-board reservation."""

import contextlib
import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def cached_board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "board.db"
    kbc.connect(path).close()
    monkeypatch.setattr(kbc, "_INIT_LOCK_TIMEOUT_SECONDS", 0)
    yield path
    kbc._INITIALIZED_PATHS.discard(str(path.resolve()))


@pytest.mark.parametrize("replacement", ["removed-during-open", "empty-before-open"])
def test_cached_schema_loss_respects_reservation(cached_board, monkeypatch, replacement):
    original = kbc._sqlite_connect
    with contextlib.ExitStack() as held:
        def remove_then_open(path, **kwargs):
            monkeypatch.setattr(kbc, "_sqlite_connect", original)
            path.unlink()
            held.enter_context(kbc._cross_process_init_lock(path, strict=True))
            return original(path, **kwargs)

        if replacement == "removed-during-open":
            monkeypatch.setattr(kbc, "_sqlite_connect", remove_then_open)
            error = sqlite3.OperationalError
        else:
            cached_board.unlink()
            sqlite3.connect(cached_board).close()
            held.enter_context(kbc._cross_process_init_lock(cached_board, strict=True))
            error = TimeoutError
        with pytest.raises(error):
            kbc.connect(cached_board).close()
        if replacement == "removed-during-open":
            assert not cached_board.exists()
        else:
            with sqlite3.connect(cached_board) as conn:
                assert conn.execute("SELECT name FROM sqlite_master WHERE name='tasks'").fetchone() is None
    conn = kbc.connect(cached_board)
    try:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    finally:
        conn.close()


def test_existing_cached_board_keeps_fast_path(cached_board):
    with kbc._cross_process_init_lock(cached_board, strict=True):
        conn = kbc.connect(cached_board)
        try:
            assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        finally:
            conn.close()
