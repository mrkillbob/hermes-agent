"""Qualify resolved Nous primary restoration without OAuth or provider traffic."""
import pytest

pytest_plugins = ['tests.agent.test_startup_fallback_constructor']


@pytest.mark.parametrize('fallback_provider,fallback_model', [
    ('nvidia', 'z-ai/glm-5.3-flash'),
    ('nim', 'nvidia/nemotron-3-ultra-550b-a55b'),
    ('llamacpp', 'Qwen3.5-4B-Q6_K'),
])
def test_resolved_nous_primary_restores_after_fallback(
    offline_runtime, monkeypatch, fallback_provider, fallback_model,
):
    import httpx
    original_transport = httpx.HTTPTransport.handle_request

    def fixture_catalog(transport, request):
        if request.method == 'GET' and str(request.url) in {
            'https://openrouter.ai/api/v1/models', 'https://fallback.invalid/v1/models',
        }:
            return httpx.Response(200, json={'data': [
                {'id': 'meituan/longcat-2.5-preview:free', 'context_length': 131072},
                {'id': 'z-ai/glm-5.3-flash', 'context_length': 131072},
                {'id': 'nvidia/nemotron-3-ultra-550b-a55b', 'context_length': 131072},
                {'id': 'Qwen3.5-4B-Q6_K', 'context_length': 131072},
                {'id': 'deepseek-flash', 'context_length': 131072},
                {'id': 'gpt-4.1-mini', 'context_length': 131072},
            ]}, request=request)
        return original_transport(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', fixture_catalog)
    from run_agent import AIAgent
    from hermes_state import SessionDB

    home, requests = offline_runtime
    database = SessionDB(db_path=home / 'state.db')
    agent = AIAgent(session_db=database, provider='nous',
        model='meituan/longcat-2.5-preview:free', api_key='synthetic-primary-key',
        base_url='https://api.openai.com/v1', api_mode='chat_completions',
        fallback_model=[dict(provider=fallback_provider, model=fallback_model,
            base_url='https://fallback.invalid/v1', key_env='SYNTHETIC_FALLBACK_API_KEY')],
        enabled_toolsets=[], skip_memory=True, skip_context_files=True,
        skip_background_review=True, quiet_mode=True, max_iterations=1,
        save_trajectories=False, checkpoints_enabled=False, cwd=str(home))
    try:
        assert agent._primary_runtime['provider'] == 'nous'
        assert agent._try_activate_fallback(reason='synthetic primary unavailable') is True
        assert agent.provider != 'nous' and agent.model == fallback_model
        assert agent._restore_primary_runtime() is True
        assert agent.provider == agent.context_compressor.provider == 'nous'
        assert agent.model == 'meituan/longcat-2.5-preview:free'
        assert agent._fallback_activated is False
        assert not requests
    finally:
        agent.close()
        database.close()


def test_unresolved_nous_startup_does_not_enter_api_key_preparation(offline_runtime, monkeypatch):
    from types import SimpleNamespace
    from agent.startup_primary_recovery import pending_primary_intent

    def unexpected_refresh(*args, **kwargs):
        pytest.fail('qualification must not refresh OAuth credentials')

    monkeypatch.setattr('hermes_cli.auth.resolve_nous_runtime_credentials', unexpected_refresh)
    assert pending_primary_intent(SimpleNamespace(provider='nous', requested_provider='nous',
        base_url='', api_mode='chat_completions', model='meituan/longcat-2.5-preview:free')) is None


@pytest.mark.parametrize('primary,recovers', [('nous', False), ('openai-api', True)])
def test_startup_fallback_reports_switch_once_after_success(offline_runtime, monkeypatch, primary, recovers):
    from run_agent import AIAgent
    from hermes_state import SessionDB

    home, requests = offline_runtime
    database = SessionDB(db_path=home / 'state.db')
    statuses = []
    agent = AIAgent(session_db=database, provider=primary, model='gpt-4.1-mini',
        fallback_model=[dict(provider='deepseek', model='deepseek-flash',
            base_url='https://fallback.invalid/v1', key_env='SYNTHETIC_FALLBACK_API_KEY')],
        status_callback=lambda kind, text: statuses.append((kind, str(text))),
        enabled_toolsets=[], skip_memory=True, skip_context_files=True,
        skip_background_review=True, quiet_mode=True, max_iterations=1,
        save_trajectories=False, checkpoints_enabled=False, cwd=str(home))
    try:
        assert agent.provider == 'deepseek'
        assert not requests
        if recovers:
            monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-primary-key')
            assert agent._restore_primary_runtime() is True
            # A terminal failure after restoration must not replay the stale switch.
            agent._flush_status_buffer()
        history = None
        for message in ('first synthetic turn', 'second synthetic turn'):
            result = agent.run_conversation(message, system_message='Answer briefly.',
                                            conversation_history=history)
            assert result.get('failed') is not True, result
            history = result['messages']
        switches = [text for _, text in statuses if 'Model fallback:' in text]
        assert len(switches) == 1
        assert f'gpt-4.1-mini via {primary}' in switches[0]
        assert 'deepseek-flash via deepseek' in switches[0]
        assert len(requests) == 2
        expected_model = 'gpt-4.1-mini' if recovers else 'deepseek-flash'
        assert all(request['model'] == expected_model for request in requests)
        if recovers:
            route_notices = [text for _, text in statuses
                             if 'Model fallback:' in text or 'Primary model restored:' in text]
            assert len(route_notices) == 2
            assert 'Model fallback:' in route_notices[0]
            assert 'Primary model restored:' in route_notices[1]
    finally:
        agent.close()
        database.close()
