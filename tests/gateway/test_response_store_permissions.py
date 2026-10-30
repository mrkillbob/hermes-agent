"""Response-store history is private before SQLite opens the database."""

import os
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
@pytest.mark.parametrize("sidecars_exist", [False, True])
def test_permission_repair_failure_keeps_existing_persistence(
    tmp_path, monkeypatch, caplog, sidecars_exist
):
    path = tmp_path / "response_store.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE existing_history (value TEXT)")
    connection.execute("INSERT INTO existing_history VALUES ('inert history marker')")
    connection.commit()
    connection.close()
    path.chmod(0o600)
    sidecars = [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm")]
    if sidecars_exist:
        for candidate in sidecars:
            candidate.touch(mode=0o600)
    chmod = os.chmod

    def readonly_chmod(candidate, mode, **kwargs):
        if str(candidate) in {str(path), *(str(sidecar) for sidecar in sidecars)}:
            raise PermissionError("chmod unavailable on test mount")
        return chmod(candidate, mode, **kwargs)

    monkeypatch.setattr(os, "chmod", readonly_chmod)
    store = ResponseStore(db_path=str(path))
    try:
        assert store._db_path == str(path)
        assert store._conn.execute("SELECT value FROM existing_history").fetchone() == ("inert history marker",)
        assert "Could not restrict response store permissions" in caplog.text
        store.put("new-history", {"output": "private disk marker"})
    finally:
        store._conn.close()
    reopened = ResponseStore(db_path=str(path))
    try:
        assert reopened._db_path == str(path)
        assert reopened.get("new-history") == {"output": "private disk marker"}
    finally:
        reopened.close()


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("broad_suffix", ["", "-wal", "-shm"])
def test_failed_repair_of_broad_files_uses_memory_without_disk_connect(
    tmp_path, monkeypatch, broad_suffix
):
    path = tmp_path / "response_store.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE existing_history (value TEXT)")
    connection.execute("INSERT INTO existing_history VALUES ('legacy inert marker')")
    connection.commit()
    connection.close()
    files = [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]
    for candidate in files[1:]:
        candidate.write_bytes(b"legacy inert sidecar marker")
    for candidate in files:
        candidate.chmod(0o644 if str(candidate) == str(path) + broad_suffix else 0o600)
    before = {candidate: candidate.read_bytes() for candidate in files}
    chmod = os.chmod
    connect = sqlite3.connect
    disk_calls = []

    def denied_chmod(candidate, mode, **kwargs):
        if str(candidate) in {str(item) for item in files}:
            raise PermissionError("chmod unavailable on test mount")
        return chmod(candidate, mode, **kwargs)

    def recorded_connect(database, *args, **kwargs):
        if str(database) == str(path):
            disk_calls.append(str(database))
            # Do not let a broken preflight mutate the inert legacy fixture.
            raise sqlite3.OperationalError("test guard: disk connect forbidden")
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", denied_chmod)
    monkeypatch.setattr(sqlite3, "connect", recorded_connect)
    store = ResponseStore(db_path=str(path))
    try:
        # Constructor fallback catches connection errors, so check this outside the spy.
        assert disk_calls == []
        assert store._db_path is None
        assert {candidate: candidate.read_bytes() for candidate in files} == before
        store.put("new-history", {"output": "memory marker"})
        assert store.get("new-history") == {"output": "memory marker"}
        assert {candidate: candidate.read_bytes() for candidate in files} == before
    finally:
        store.close()


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("fault", ["missing-main", "unreadable-main", "nonregular-sidecar"])
def test_unestablished_privacy_uses_memory_without_disk_connect(tmp_path, monkeypatch, fault):
    path = tmp_path / "response_store.db"
    files = []
    if fault != "missing-main":
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE existing_history (value TEXT)")
        connection.execute("INSERT INTO existing_history VALUES ('legacy inert marker')")
        connection.commit()
        connection.close()
        path.chmod(0o600)
        files.append(path)
    wal = path.with_name(path.name + "-wal")
    if fault == "nonregular-sidecar":
        wal.mkdir(mode=0o700)
        files.append(wal / "inert-marker")
    else:
        files.append(wal)
    files.append(path.with_name(path.name + "-shm"))
    for candidate in files:
        if candidate != path:
            candidate.write_bytes(b"legacy inert sidecar marker")
            candidate.chmod(0o600)
    before = {candidate: candidate.read_bytes() for candidate in files}
    open_file, lstat, chmod, connect = os.open, os.lstat, os.chmod, sqlite3.connect
    disk_calls = []

    def denied_creation(candidate, *args, **kwargs):
        if fault == "missing-main" and str(candidate) == str(path):
            raise PermissionError("test mount refuses main creation")
        return open_file(candidate, *args, **kwargs)

    def unavailable_metadata(candidate, *args, **kwargs):
        if fault == "unreadable-main" and str(candidate) == str(path):
            raise PermissionError("test mount refuses main metadata")
        return lstat(candidate, *args, **kwargs)

    def denied_chmod(candidate, mode, **kwargs):
        if str(candidate) == str(path):
            raise PermissionError("chmod unavailable on test mount")
        return chmod(candidate, mode, **kwargs)

    def recorded_connect(database, *args, **kwargs):
        if str(database) == str(path):
            disk_calls.append(str(database))
            raise sqlite3.OperationalError("test guard: disk connect forbidden")
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied_creation)
    monkeypatch.setattr(os, "lstat", unavailable_metadata)
    monkeypatch.setattr(os, "chmod", denied_chmod)
    monkeypatch.setattr(sqlite3, "connect", recorded_connect)
    store = ResponseStore(db_path=str(path))
    try:
        assert disk_calls == []
        assert store._db_path is None
        store.put("new-history", {"output": "memory marker"})
        assert store.get("new-history") == {"output": "memory marker"}
        assert {candidate: candidate.read_bytes() for candidate in files} == before
        if fault == "missing-main":
            assert not path.exists()
        elif fault == "nonregular-sidecar":
            assert wal.is_dir()
    finally:
        store.close()
