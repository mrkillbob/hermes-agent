"""Implicit terminal hints skip only provably empty, unadmitted launches."""
from types import SimpleNamespace

import pytest

from tests.gateway.test_resume_latest_local_cli import _owner, _call
from tests.gateway.breadcrumb_test_support import _fence_unrelated_effects, _absent, _preflight


@pytest.mark.asyncio
@pytest.mark.parametrize('admission', [None, 'queued'])
async def test_terminal_breadcrumb_skips_only_unadmitted_empty_launches(tmp_path, monkeypatch, admission):
    attempted = _fence_unrelated_effects(monkeypatch)
    from hermes_cli import gateway_chat, terminal_breadcrumbs
    from hermes_cli.gateway_connection import GatewayClientError

    async def no_inventory(*args, **kwargs):
        from gateway.session_contract import CANONICAL_GATEWAY_PROTOCOL
        return {'desktop_protocol': CANONICAL_GATEWAY_PROTOCOL}

    monkeypatch.setattr('gateway.session_local_inventory.snapshot_info', no_inventory)
    store, authority, owner = _owner(tmp_path / 'home', monkeypatch)
    try:
        ids = []
        for name in ('real', 'breadcrumb'):
            created = await _call(owner, 'session.create', request_id=name, source='cli',
                                  cwd=str(tmp_path), toolsets=[], model='m')
            sid = created['session_id']
            ids.append(sid)
            authority.db.create_session(sid, source='cli')
            live = authority.sessions[sid]
            store._record_gateway_session_peer(sid, live.route, live.source)
        real, breadcrumb = ids
        authority.db.append_message(real, 'user', 'hello')
        if admission:
            with authority.db._lock:
                authority.db._conn.execute(
                    "INSERT INTO session_admissions(admission_id,request_id,principal_id,target_session_id,"
                    "lineage_json,payload_json,payload_digest,intent,status) "
                    "VALUES('adm','req','human',?,'[]','{}','d','queue',?)", (breadcrumb, admission))
                authority.db._conn.commit()
        authority.sessions.clear()
        monkeypatch.setattr(terminal_breadcrumbs, 'read_breadcrumb', lambda: {'session_id': breadcrumb})
        monkeypatch.setattr(gateway_chat, '_workspace_key', lambda cwd: None)
        class Client:
            async def rpc(self, method, **params):
                result = await _call(owner, method, **params)
                if isinstance(result, str):
                    raise GatewayClientError(result)
                return result
        snapshot = await gateway_chat._resume_latest(Client(), SimpleNamespace(continue_last=True, api_key=None))
        assert snapshot['session_id'] == (breadcrumb if admission else real)
    finally:
        try:
            await owner.close()
        finally:
            store.close_all_db_handles()
    assert attempted == [], 'unexpected excluded process/network probe, including cleanup'
    _absent(_preflight())
