"""Per-model OpenRouter fallback preferences survive both request builders."""

import json
from types import SimpleNamespace

import pytest

from agent import chat_completion_helpers as cch
from agent.chat_completion_summary import iteration_summary_chat_kwargs
from agent.transports.chat_completions import ChatCompletionsTransport


MODEL = "example/routing-test-model"
PIN = ["preferred-provider"]


@pytest.fixture
def routing_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    def configure(entry):
        # A different disk default must not replace the agent's constructor snapshot.
        config = {"provider_routing": {"sort": "latency", "models": {MODEL: entry}}}
        (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")

    return configure


def _agent(provider="openrouter"):
    transport = ChatCompletionsTransport()
    base_url = "https://openrouter.ai/api/v1"
    return SimpleNamespace(
        model=MODEL, provider=provider, base_url=base_url, _base_url_lower=base_url,
        _base_url_hostname="openrouter.ai", session_id=None, reasoning_config={"enabled": True},
        providers_allowed=None, providers_ignored=None, providers_order=None, provider_sort="price",
        provider_require_parameters=False, provider_data_collection=None, max_tokens=None,
        _ollama_num_ctx=None, openrouter_min_coding_score=None,
        _get_transport=lambda: transport, _is_qwen_portal=lambda: False,
        _is_openrouter_url=lambda: True, _supports_reasoning_extra_body=lambda: False,
        _prepare_messages_for_non_vision_model=lambda messages: messages,
        _resolved_api_call_timeout=lambda: 30, _max_tokens_param=lambda value: {"max_tokens": value},
    )


@pytest.mark.parametrize(("entry", "expected"), [
    ({"only": PIN, "allow_fallbacks": False}, {"only": PIN, "allow_fallbacks": False}),
    ({"allow_fallbacks": False}, {"allow_fallbacks": False}),
    ({"allow_fallbacks": True}, {"allow_fallbacks": True}),
    ({"only": PIN}, {"only": PIN}),
    ({"allow_fallbacks": "false"}, {}),
    ({"allow_fallbacks": 0}, {}),
])
def test_per_model_fallback_boolean_is_validated_and_model_scoped(routing_home, entry, expected):
    routing_home(entry)
    agent = _agent()
    assert cch._provider_preferences_for_agent(agent) == {"sort": "price", **expected}
    agent.model = "example/another-model"
    assert cch._provider_preferences_for_agent(agent) == {"sort": "price"}


@pytest.mark.parametrize("allow_fallbacks", [False, True, None])
@pytest.mark.parametrize("provider", ["openrouter", "unregistered-routing-test"])
@pytest.mark.parametrize("request_kind", ["chat", "iteration_summary"])
def test_per_model_fallback_preference_reaches_request_kwargs(
    routing_home, allow_fallbacks, provider, request_kind,
):
    entry = {"only": PIN}
    if allow_fallbacks is not None:
        entry["allow_fallbacks"] = allow_fallbacks
    routing_home(entry)
    agent = _agent(provider)
    messages = [{"role": "user", "content": "synthetic request"}]
    if provider == "openrouter":
        from providers import get_provider_profile
        assert get_provider_profile(provider) is not None
    if request_kind == "chat":
        kwargs = cch._build_chat_completions_kwargs(agent, messages, None, None, {}, None)
    else:
        kwargs = iteration_summary_chat_kwargs(agent, messages)
    assert kwargs["extra_body"]["provider"] == {"sort": "price", **entry}
