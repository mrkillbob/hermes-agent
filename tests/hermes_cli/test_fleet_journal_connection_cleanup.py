"""Intent-journal setup errors close only the transient connection."""

import sqlite3
from contextlib import closing

import pytest

from hermes_cli import fleet_claim_journal
from hermes_cli.fleet_protocol import ProtocolError


def test_setup_failure_closes_connection_and_preserves_owner(monkeypatch, tmp_path):
    journal = fleet_claim_journal.ClaimJournal(
        tmp_path, node_id="test-node", coordinator="test-coordinator"
    )
    connect = sqlite3.connect
    connection = connect(journal.path)
    connection.set_authorizer(
        lambda action, name, *_: (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == "synchronous"
            else sqlite3.SQLITE_OK
        )
    )
    monkeypatch.setattr(fleet_claim_journal.sqlite3, "connect", lambda *a, **kw: connection)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            with journal._connection():
                pytest.fail("setup failure must prevent entering the body")
        assert journal._owner.in_transaction
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")
    finally:
        connection.close()
        journal.close()


def test_body_failure_rolls_back_and_closes_connection(monkeypatch, tmp_path):
    journal = fleet_claim_journal.ClaimJournal(
        tmp_path, node_id="test-node", coordinator="test-coordinator"
    )
    connect = sqlite3.connect
    connection = connect(journal.path)
    connection.execute("CREATE TABLE probe (value TEXT)")
    connection.commit()
    monkeypatch.setattr(fleet_claim_journal.sqlite3, "connect", lambda *a, **kw: connection)
    try:
        with pytest.raises(ValueError, match="fixture body failure"):
            with journal._connection() as current:
                current.execute("INSERT INTO probe VALUES ('uncommitted')")
                raise ValueError("fixture body failure")
        assert journal._owner.in_transaction
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")
        with closing(connect(journal.path)) as observer:
            assert observer.execute("SELECT value FROM probe").fetchall() == []
    finally:
        connection.close()
        journal.close()


def test_closed_owner_rejects_connection_before_open(monkeypatch, tmp_path):
    journal = fleet_claim_journal.ClaimJournal(
        tmp_path, node_id="test-node", coordinator="test-coordinator"
    )
    journal.close()

    def unexpected_connect(*args, **kwargs):
        pytest.fail("an unowned journal must not open a connection")

    monkeypatch.setattr(fleet_claim_journal.sqlite3, "connect", unexpected_connect)
    with pytest.raises(ProtocolError, match="not exclusively owned"):
        with journal._connection():
            pytest.fail("an unowned journal must not enter the body")
