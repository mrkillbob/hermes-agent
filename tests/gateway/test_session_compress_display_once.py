"""Canonical compression must not display an original beside its rewritten tail carrier."""
from collections import Counter
import re
from types import SimpleNamespace

import pytest

from agent.context_compressor import ContextCompressor
from gateway import run
from gateway.config import GatewayConfig
from gateway.session import SessionStore
from gateway.session_authority import SessionAuthority
from gateway.session_controls import AuthorityConnection
from gateway.session_local import create_local_session
import hermes_state_runtime as rt


@pytest.mark.asyncio
@pytest.mark.parametrize('focus', ['', 'here 1', 'here 2'])
@pytest.mark.parametrize('system_in_tail', [False, True])
async def test_compress_and_resume_keep_each_original_reply_once(tmp_path, monkeypatch, focus, system_in_tail):
    monkeypatch.setattr(run, '_load_gateway_config', lambda *args: {
        'compression': {'protect_first_n': 3, 'protect_last_n': 2, 'min_tail_user_messages': 1},
        'platform_toolsets': {'cli': []},
    })
    monkeypatch.setattr(ContextCompressor, '_call_summary_llm',
                        lambda self, prompt, started_at: 'SUMMARYMARK compressed account of the earlier turns')
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None, _evict_cached_agent=lambda *a, **kw: None,
                             _resolve_session_agent_runtime=lambda **kwargs: ('fake-model', {
                                 'provider': 'custom', 'base_url': 'http://127.0.0.1:9/v1',
                                 'api_key': 'fixture-only'}))
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = SessionAuthority(runner, profile_id=str(tmp_path), instance_id='owner', db=db,
                                 epoch=rt.begin_runtime_epoch(db, instance_id='owner'))
    owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
    try:
        ref = create_local_session(authority, owner.actor, dict(
            request_id='transcript', source='cli', cwd=str(tmp_path), model='fake-model', toolsets=[]))
        system_row = None
        for turn in range(1, 8):
            db.append_message(ref.session_id, 'user', f'long session turn zq{turn}q')
            db.append_message(ref.session_id, 'assistant', ' '.join(f's{turn}w{i:03d}' for i in range(200)))
            if system_in_tail and turn == 6:
                system_row = db.append_message(ref.session_id, 'system', 'SYSTEMMARK retained session note')
        before = db.get_session(ref.session_id)
        response = await owner.dispatch({'id': 1, 'method': 'session.mutate', 'params': {
            'session_id': ref.session_id, 'request_id': 'compress', 'operation': 'compress', 'payload': {'focus': focus},
            'expected_revision': before['runtime_revision'], 'expected_generation': before['runtime_generation']}})
        assert 'result' in response, response
        assert response['result']['message_count'] < 14
        expected = Counter({f's{turn}w{i:03d}': 1 for turn in range(1, 8) for i in range(200)})
        for _ in range(2):
            resumed = await owner.dispatch({'id': 2, 'method': 'session.resume',
                                            'params': {'session_id': ref.session_id}})
            assert 'result' in resumed, resumed
            text = '\n'.join(str(m.get('content', '')) for m in resumed['result']['messages'])
            assert Counter(re.findall(r'\bs\dw\d{3}\b', text)) == expected
            assert text.count('SYSTEMMARK') == int(system_in_tail)
        if system_in_tail:
            # This row never reached the compressor, so it cannot be a superseded tail copy.
            assert system_row is not None
            assert tuple(db._read_one('SELECT active, compacted FROM messages WHERE id=?', (system_row,))) == (0, 1)
    finally:
        await owner.close()
        store.close_all_db_handles()
