"""A usage-window response must not bypass the fork's physical request ceiling."""
import json
from types import SimpleNamespace

import httpx
from openai import OpenAI
import pytest


def test_nonstreaming_window_headers_keep_the_frozen_request_budget(monkeypatch):
    from agent import managed_request_budget as budgets
    from agent.chat_completion_helpers import _dispatch_nonstreaming_api_request
    from tests.agent.test_credits_usage_window import _window_headers

    monkeypatch.setattr(budgets, "_budget", None)
    # This contract isolates HTTP accounting from the separate egress-policy suite.
    monkeypatch.setattr("agent.chat_completion_helpers._destination_requires_egress_firewall", lambda _agent: False)
    budgets.bind_worker_request_budget({"policy": {"kanban_json": json.dumps({
        "execution_budget": {"wall_seconds": 60, "output_bytes": 1024, "requests": 1},
    })}})
    sent, captured = [], []

    def send(request):
        sent.append(request.url.path)
        return httpx.Response(200, headers=_window_headers(),
                              json={"id": "fixture", "choices": [], "model": "fixture"})

    with OpenAI(api_key="fixture", base_url="https://fixture.invalid/v1", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(send))) as client:
        agent = SimpleNamespace(
            api_mode="chat_completions", provider="nous", base_url=str(client.base_url),
            _credits_state=SimpleNamespace(is_usage_window=True),
            _capture_rate_limits=lambda raw: None,
            _capture_credits=lambda raw: captured.append(raw.headers["x-nous-credits-denominator-kind"]),
            _capture_nous_model_switch=lambda raw: None,
            _check_openrouter_cache_status=lambda raw: None,
        )
        dispatch = lambda: _dispatch_nonstreaming_api_request(
            agent, {"model": "fixture", "messages": []}, make_client=lambda *args: client)
        assert dispatch().id == "fixture"
        assert captured == ["usage_window"]
        with pytest.raises(Exception):
            dispatch()
    assert sent == ["/v1/chat/completions"]
    assert budgets.stamp_request_budget_result({"completed": True})["completed"] is False
