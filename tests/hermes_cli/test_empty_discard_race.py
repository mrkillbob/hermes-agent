"""Thin-client empty cleanup must not delete content committed after its probe."""
from types import SimpleNamespace

import pytest

from hermes_cli.gateway_client import GatewayRPCError
from hermes_cli.gateway_session_store import GatewaySessionStore
from hermes_state import SessionDB
import hermes_state_runtime as runtime


@pytest.mark.parametrize('arrival', ['message', 'title', 'child'])
def test_empty_discard_rechecks_at_owner_transaction(tmp_path, monkeypatch, arrival):
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('empty', source='cli')
    epoch = runtime.begin_runtime_epoch(db, instance_id='fixture')
    store = GatewaySessionStore(SimpleNamespace(states={}, final=False))
    monkeypatch.setattr(store, '_db', lambda: db)

    def rpc(method, **params):
        if method == 'session.resume':
            # The CLI's read-only empty probe already returned; a peer commits
            # useful state before the owner hands back the latest revision.
            if arrival == 'message':
                db.append_message('empty', 'user', 'Keep this conversation')
            elif arrival == 'title':
                db.set_session_title('empty', 'Keep this title')
            else:
                db.create_session('child', source='tool', parent_session_id='empty')
            row = db.get_session('empty')
            return {'session_id': 'empty', 'revision': row['runtime_revision'],
                    'execution_generation': row['runtime_generation']}
        if method == 'session.mutate':
            try:
                return runtime.mutate_runtime_session(db, epoch=epoch, principal_id='human', **params)
            except runtime.RuntimeStoreError as exc:
                raise GatewayRPCError(str(exc)) from exc
        raise AssertionError(method)

    monkeypatch.setattr(store, 'call', rpc)
    try:
        assert store.delete_session_if_empty('empty') is False
        assert db.get_session('empty') is not None
        if arrival == 'message':
            assert db.get_messages('empty')[0]['content'] == 'Keep this conversation'
        elif arrival == 'title':
            assert db.get_session_title('empty') == 'Keep this title'
        else:
            assert db.get_session('child')['parent_session_id'] == 'empty'
    finally:
        db.close()
