"""Fleet connections must close when setup or the transaction body fails."""

import sqlite3
from contextlib import closing

import pytest

from hermes_cli import fleet_store


@pytest.mark.parametrize("pragma", ["busy_timeout", "foreign_keys", "synchronous"])
def test_setup_failure_closes_connection_without_entering_body(monkeypatch, pragma):
    connection = sqlite3.connect(":memory:")
    connection.set_authorizer(
        lambda action, name, *_: (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == pragma
            else sqlite3.SQLITE_OK
        )
    )
    monkeypatch.setattr(fleet_store.sqlite3, "connect", lambda *a, **kw: connection)
    store = object.__new__(fleet_store.FleetStore)
    store.path = ":memory:"

    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            with store._connection():
                pytest.fail("setup failure must prevent entering the transaction body")
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")
    finally:
        connection.close()


def test_body_failure_rolls_back_and_closes_connection(monkeypatch, tmp_path):
    path = tmp_path / "fleet.db"
    connect = sqlite3.connect
    connection = connect(path)
    connection.execute("CREATE TABLE probe (value TEXT)")
    connection.commit()
    monkeypatch.setattr(fleet_store.sqlite3, "connect", lambda *a, **kw: connection)
    store = object.__new__(fleet_store.FleetStore)
    store.path = str(path)

    with pytest.raises(ValueError, match="fixture body failure"):
        with store._connection() as current:
            current.execute("INSERT INTO probe VALUES ('uncommitted')")
            raise ValueError("fixture body failure")

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
    with closing(connect(path)) as observer:
        assert observer.execute("SELECT value FROM probe").fetchall() == []
