"""Response-store history is private before SQLite opens the database."""

import sqlite3
import stat

import pytest

from gateway.platforms.api_server import ResponseStore


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("existing", [False, True])
def test_response_store_is_private_before_sqlite_connect(tmp_path, monkeypatch, existing):
    path = tmp_path / "response_store.db"
    if existing:
        sqlite3.connect(path).close()
        path.chmod(0o644)
    connect = sqlite3.connect

    def checked_connect(database, *args, **kwargs):
        if str(database) == str(path):
            assert path.exists()
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", checked_connect)
    store = ResponseStore(db_path=str(path))
    try:
        assert store._db_path == str(path)
        for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            if candidate.exists():
                assert stat.S_IMODE(candidate.stat().st_mode) == 0o600
    finally:
        store._conn.close()


@pytest.mark.platforms("posix")
def test_permission_repair_failure_keeps_existing_persistence(tmp_path, monkeypatch, caplog):
    import os

    path = tmp_path / "response_store.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE existing_history (value TEXT)")
    connection.execute("INSERT INTO existing_history VALUES ('inert history marker')")
    connection.commit()
    connection.close()
    path.chmod(0o644)
    chmod = os.chmod

    def readonly_chmod(candidate, mode, **kwargs):
        if str(candidate) == str(path):
            raise PermissionError("chmod unavailable on test mount")
        return chmod(candidate, mode, **kwargs)

    monkeypatch.setattr(os, "chmod", readonly_chmod)
    store = ResponseStore(db_path=str(path))
    try:
        assert store._db_path == str(path)
        assert store._conn.execute("SELECT value FROM existing_history").fetchone() == ("inert history marker",)
        assert "Could not restrict response store permissions" in caplog.text
    finally:
        store._conn.close()
