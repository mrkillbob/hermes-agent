"""The primary client may resolve; fallback and secondary clients may not."""
from types import SimpleNamespace

import pytest


def test_main_resolution_is_narrow_and_fallback_is_disabled(monkeypatch):
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    from agent import agent_init, auxiliary_client
    from agent.agent_init_fallback import _fallback_entries
    from agent.route_binding import bind_route_entry
    from agent.local_text_policy import LocalTextRestriction, local_text_scope, require_client_allowed
    monkeypatch.delenv('HERMES_KANBAN_LOCAL_ONLY', raising=False)
    entries = [{'provider': 'nvidia', 'model': 'fixture'}]
    assert _fallback_entries(entries) == entries
    captured = []
    def client(*args, **kwargs):
        require_client_allowed()
        captured.append(contextvars.copy_context())
        with ThreadPoolExecutor(1) as executor:
            with pytest.raises(LocalTextRestriction):
                executor.submit(contextvars.copy_context().run, require_client_allowed).result()
        return SimpleNamespace(api_key='fixture', base_url='http://127.0.0.1:8000/v1'), 'public-model'
    monkeypatch.setattr(auxiliary_client, 'resolve_provider_client', client)
    with local_text_scope(True):
        assert _fallback_entries(entries) == []
        assert bind_route_entry(None, entries[0], 'nvidia', 'fixture') is None
        kwargs = agent_init._routed_client_kwargs(SimpleNamespace(provider='llamacpp', model='public-model'), entries, 5)
        assert kwargs['base_url'] == 'http://127.0.0.1:8000/v1'
        with pytest.raises(LocalTextRestriction): require_client_allowed()
        with pytest.raises(LocalTextRestriction): captured[0].run(require_client_allowed)
    assert _fallback_entries(entries) == entries


def test_physical_requests_cannot_switch_endpoint_or_model(monkeypatch):
    import httpx
    from agent import managed_request_budget as budget
    route = ('http', '127.0.0.1', 8000, 'public-model')
    for endpoint, model, allowed in [('http://127.0.0.1:8000/v1/chat/completions', 'public-model', True),
            ('https://remote.invalid/v1/chat/completions', 'public-model', False),
            ('http://127.0.0.1:8000/v1/chat/completions', 'substituted', False)]:
        state = budget._RequestBudget(1, route)
        monkeypatch.setattr(budget, '_budget', state)
        request = httpx.Request('POST', endpoint, json={'model': model, 'messages': []})
        if allowed:
            budget._request_hook(request)
            assert state.used == 1
            with pytest.raises(budget.RequestBudgetExhausted): budget._request_hook(request)
        else:
            with pytest.raises(budget.RequestBudgetExhausted): budget._request_hook(request)
            assert state.used == 0 and state.reason == 'local_text_route_mismatch'
