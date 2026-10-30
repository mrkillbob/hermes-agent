"""Synthetic controls for the implicit terminal-breadcrumb contract."""
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


class MemoryDB:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT)')
        self.conn.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, message_count INTEGER, end_reason TEXT)')
        self.conn.execute('CREATE TABLE session_admissions (target_session_id TEXT, status TEXT)')
        self.conn.executemany('INSERT INTO sessions VALUES (?, ?, ?)', [('owner', 1, 'session_reset'), ('child', 0, None)])
        self.conn.execute('INSERT INTO state_meta VALUES (?, ?)', ('gateway.local_policy.v1:owner',
            json.dumps({'session_id': 'owner', 'lineage': ['owner', 'child'], 'entry': {'session_id': 'child'}})))
        self.conn.commit()

    @contextmanager
    def _read_ctx(self):
        yield self.conn


@pytest.mark.parametrize('status', ['queued', 'started', 'unknown', 'terminal'])
def test_logical_fifo_admission_preserves_reset_breadcrumb(monkeypatch, status):
    from gateway.session_local_title import eligible_breadcrumb
    monkeypatch.setattr('hermes_state_local_lineage.validate_local_lineage', lambda conn, receipt: 'child')
    db = MemoryDB()
    try:
        db.conn.execute('INSERT INTO session_admissions VALUES (?, ?)', ('owner', status))
        db.conn.commit()
        assert eligible_breadcrumb(SimpleNamespace(db=db), 'owner') is True
    finally:
        db.conn.close()


def test_current_empty_reset_not_made_eligible_by_old_message(monkeypatch):
    from gateway.session_local_title import eligible_breadcrumb
    monkeypatch.setattr('hermes_state_local_lineage.validate_local_lineage', lambda conn, receipt: 'child')
    db = MemoryDB()
    try:
        assert eligible_breadcrumb(SimpleNamespace(db=db), 'owner') is False
        db.conn.execute('UPDATE sessions SET message_count=1 WHERE id=?', ('child',))
        db.conn.commit()
        assert eligible_breadcrumb(SimpleNamespace(db=db), 'owner') is True
    finally:
        db.conn.close()


def test_missing_or_unverified_lineage_preserves_hint(monkeypatch):
    from gateway.session_local_title import eligible_breadcrumb
    from hermes_state_runtime import RuntimeStoreError
    db = MemoryDB()
    try:
        db.conn.execute('DELETE FROM state_meta')
        db.conn.commit()
        assert eligible_breadcrumb(SimpleNamespace(db=db), 'owner') is True
        db.conn.execute('INSERT INTO state_meta VALUES (?, ?)', ('gateway.local_policy.v1:owner',
            json.dumps({'session_id': 'owner', 'lineage': ['owner', 'child'], 'entry': {'session_id': 'child'}})))
        db.conn.commit()
        monkeypatch.setattr('hermes_state_local_lineage.validate_local_lineage',
                            lambda conn, receipt: (_ for _ in ()).throw(RuntimeStoreError('storage_unavailable')))
        assert eligible_breadcrumb(SimpleNamespace(db=db), 'owner') is True
    finally:
        db.conn.close()


def test_legacy_store_without_admission_table_preserves_hint(monkeypatch):
    from gateway.session_local_title import eligible_breadcrumb
    monkeypatch.setattr('hermes_state_local_lineage.validate_local_lineage', lambda conn, receipt: 'child')
    db = MemoryDB()
    try:
        db.conn.execute('DROP TABLE session_admissions')
        db.conn.commit()
        assert eligible_breadcrumb(SimpleNamespace(db=db), 'owner') is True
    finally:
        db.conn.close()


@pytest.mark.asyncio
async def test_old_owner_unknown_flag_falls_back_to_latest_only(monkeypatch):
    from hermes_cli import gateway_chat, terminal_breadcrumbs
    from hermes_cli.gateway_client import GatewayRPCError
    monkeypatch.setattr(terminal_breadcrumbs, 'read_breadcrumb', lambda: {'session_id': 'hint'})
    monkeypatch.setattr(gateway_chat, '_workspace_key', lambda cwd: None)
    seen = []

    class Client:
        async def rpc(self, method, **params):
            seen.append(params)
            if len(seen) == 1:
                raise GatewayRPCError('invalid_params', 4001, {'fields': ['breadcrumb_candidate']})
            return {'session_id': 'latest'}

    result = await gateway_chat._resume_latest(Client(), SimpleNamespace(continue_last=True, api_key=None))
    assert result['session_id'] == 'latest'
    assert seen == [{'session_id': 'hint', 'breadcrumb_candidate': True}, {'latest': 'cli'}]


@pytest.mark.asyncio
async def test_unrelated_invalid_params_is_not_swallowed(monkeypatch):
    from hermes_cli import gateway_chat, terminal_breadcrumbs
    from hermes_cli.gateway_client import GatewayRPCError
    monkeypatch.setattr(terminal_breadcrumbs, 'read_breadcrumb', lambda: {'session_id': 'hint'})

    class Client:
        async def rpc(self, method, **params):
            raise GatewayRPCError('invalid_params', 4001, {'fields': ['api_key']})

    with pytest.raises(GatewayRPCError):
        await gateway_chat._resume_latest(Client(), SimpleNamespace(continue_last=True, api_key=None))
