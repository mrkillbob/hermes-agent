"""Synthetic real-constructor integration for provider-owned non-refreshing adoption."""
import json
import time

import pytest

pytest_plugins = ['tests.agent.test_startup_fallback_constructor']
pytestmark = pytest.mark.platforms('posix')


@pytest.mark.parametrize('case', ['success', 'generation-changed', 'pool-scope', 'missing-id', 'peer-rotation', 'rate-error', 'custom-added', 'close-error'])
def test_owned_adoption_preserves_runtime_and_pool_accounting(offline_runtime, monkeypatch, case):
    import httpx
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from hermes_cli.auth_constants import DEFAULT_NOUS_INFERENCE_URL
    from tests.hermes_cli.test_auth_nous_provider import _invoke_jwt
    from agent import startup_primary_recovery as recovery

    original_transport = httpx.HTTPTransport.handle_request

    def catalogue(transport, request):
        if request.method == 'GET' and str(request.url) == DEFAULT_NOUS_INFERENCE_URL + '/models':
            return httpx.Response(200, json={'data': [
                {'id': 'gpt-4.1-mini', 'context_length': 131072}]}, request=request)
        if request.method == 'POST' and str(request.url) == DEFAULT_NOUS_INFERENCE_URL + '/chat/completions':
            body = json.loads(request.content)
            requests.append({'model': body['model'], 'authorization': request.headers['authorization']})
            return httpx.Response(200, json=dict(id='synthetic', object='chat.completion', created=0,
                model=body['model'], choices=[dict(index=0, message=dict(role='assistant', content='Synthetic Nous reply.'),
                finish_reason='stop')], usage=dict(prompt_tokens=20, completion_tokens=4, total_tokens=24)), request=request)
        return original_transport(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', catalogue)
    home, requests = offline_runtime
    db = SessionDB(db_path=home / 'state.db')
    agent = AIAgent(session_db=db, provider='nous', model='gpt-4.1-mini', api_mode='chat_completions',
        fallback_model=[dict(provider='deepseek', model='deepseek-flash',
            base_url='https://fallback.invalid/v1', key_env='SYNTHETIC_FALLBACK_API_KEY')],
        enabled_toolsets=[], skip_memory=True, skip_context_files=True, skip_background_review=True,
        quiet_mode=True, max_iterations=1, save_trajectories=False, checkpoints_enabled=False, cwd=str(home))
    fallback_client = agent.client
    token = _invoke_jwt(seconds=3600)
    row = dict(id='synthetic-owned-row', source='device_code', auth_type='oauth', access_token=token,
        refresh_token='synthetic-grant', scope='inference:invoke', last_status='ok', request_count=37,
        inference_base_url=DEFAULT_NOUS_INFERENCE_URL)
    state = dict(access_token=token, refresh_token='synthetic-grant', scope='inference:invoke',
                 inference_base_url=DEFAULT_NOUS_INFERENCE_URL)
    if case == 'pool-scope':
        row['scope'] = 'billing:manage'
        row['expires_at'] = '2000-01-01T00:00:00Z'
    if case == 'missing-id':
        row.pop('id')
    store = dict(providers={'nous': state}, credential_pool={'nous': [row]})
    path = home / 'auth.json'
    path.write_text(json.dumps(store))
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail('integration must not invoke generic credential resolver or pool loader')

    monkeypatch.setattr('agent.auxiliary_client.resolve_provider_client', forbidden)
    monkeypatch.setattr('agent.credential_pool.load_pool', forbidden)
    if case == 'rate-error':
        def unavailable_rate_state(**kwargs):
            raise PermissionError('synthetic read failure')
        monkeypatch.setattr('agent.nous_rate_guard.nous_rate_limit_remaining', unavailable_rate_state)
    if case == 'custom-added':
        monkeypatch.setattr('agent.custom_entry_pool.named_custom_entry', lambda provider: object())
    original_prepare = recovery._prepare_primary

    def prepare(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        if case == 'close-error':
            client = args[2]
            close = client.close
            def failing_close():
                close()
                raise RuntimeError('synthetic close failure')
            monkeypatch.setattr(client, 'close', failing_close)
            raise ValueError('synthetic preparation failure')
        if case == 'generation-changed':
            state['refresh_token'] = row['refresh_token'] = 'synthetic-peer-grant'
            path.write_text(json.dumps(store))
        return result

    monkeypatch.setattr(recovery, '_prepare_primary', prepare)
    try:
        adopted = agent._restore_primary_runtime()
        assert adopted is (case in ('success', 'peer-rotation'))
        assert not requests
        if not adopted:
            assert agent.client is fallback_client
            assert agent.provider == 'deepseek'
            if case != 'generation-changed':
                assert path.read_bytes() == before
            return
        assert agent.provider == agent.context_compressor.provider == 'nous'
        assert agent._credential_pool_entry_id == row['id']
        pool = agent._credential_pool
        selected = pool.current()
        assert selected.id == row['id'] and selected.request_count == 37
        assert pool._persisted_token_pairs[row['id']] == (token, 'synthetic-grant')
        assert path.read_bytes() == before  # Adoption itself never normalizes or persists auth.
        if case == 'success':
            result = agent.run_conversation('synthetic request', system_message='Answer briefly.')
            assert result['final_response'] == 'Synthetic Nous reply.'
            assert requests == [{'model': 'gpt-4.1-mini', 'authorization': 'Bearer ' + token}]
        if case == 'peer-rotation':
            state['access_token'] = row['access_token'] = _invoke_jwt(seconds=7200)
            state['refresh_token'] = row['refresh_token'] = 'synthetic-peer-grant'
            path.write_text(json.dumps(store))
        pool._mark_exhausted(selected, 429, {'reset_at': time.time() + 60})
        saved = json.loads(path.read_text())['credential_pool']['nous'][0]
        assert saved['id'] == row['id'] and saved['request_count'] == 37
        assert saved['access_token'] == row['access_token']
        assert saved['refresh_token'] == row['refresh_token']
        if case == 'success':
            assert saved['last_status'] == 'exhausted'
    finally:
        agent.close()
        db.close()


def test_read_only_rate_limit_check_preserves_expired_state(offline_runtime):
    from agent.nous_rate_guard import nous_rate_limit_remaining
    home, _ = offline_runtime
    path = home / 'rate_limits' / 'nous.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'reset_at': time.time() - 1}))
    before = path.read_bytes()
    assert nous_rate_limit_remaining(read_only=True) is None
    assert path.read_bytes() == before
