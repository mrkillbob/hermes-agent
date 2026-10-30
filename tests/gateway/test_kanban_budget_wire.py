"""The actual canonical dispatcher admits the frozen budget before the claim handler."""
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['restricted', 'null', 'absent', 'unknown'])
async def test_budget_reaches_canonical_claim_handler(tmp_path, monkeypatch, mode):
    from gateway.session_authority import SessionAuthority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_kanban_restriction import claim_config
    from hermes_state import SessionDB
    from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    with SessionDB(db_path=tmp_path / 'owner.db') as db:
        authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id=str(tmp_path),
            instance_id='fixture', db=db, epoch=begin_runtime_epoch(db, instance_id='fixture'))
        connection = AuthorityConnection(authority, object(), {'user_id':'fixture'})
        params = dict(board='default', task_id='public', run_id=1, claim_lock='claim')
        if mode == 'restricted':
            params.update(execution_budget=dict(wall_seconds=60, output_bytes=8192, requests=1, local_text_only=True),
                route=dict(provider='llamacpp', model='public', base_url='http://127.0.0.1:8000/v1'))
        if mode == 'null': params['execution_budget'] = None
        if mode == 'unknown': params['execution_budget'] = dict(wall_seconds=60, output_bytes=8192, requests=1, unrecognized=True)
        reached = []
        async def handler(ref, submitted):
            config = claim_config(submitted, lambda:{'ordinary':True})
            assert config == ({} if mode == 'restricted' else {'ordinary':True})
            reached.append(True)
            raise RuntimeStoreError('fixture_handler_reached')
        monkeypatch.setattr(connection, 'handlers', lambda:{'kanban.run':handler})
        try:
            reply = await connection.dispatch({'id':1, 'method':'kanban.run', 'params':params})
            assert reply['error']['data']['reason'] == ('invalid_params' if mode == 'unknown' else 'fixture_handler_reached')
            assert bool(reached) is (mode != 'unknown')
        finally:
            await connection.close()


@pytest.mark.parametrize('present', [False, True])
def test_null_budget_selects_ordinary_config(present):
    from gateway.session_kanban_restriction import claim_config
    seen = []
    def load():
        seen.append(True)
        return {'ordinary':True}
    assert claim_config({'execution_budget':None} if present else {}, load) == {'ordinary':True}
    assert seen == [True]
