"""Real constructor and turn entrypoints keep preferred and active runtime identities distinct."""

import json
import os
from pathlib import Path
import platform
import socket
import subprocess

import pytest


class ExternalEffectDenied(BaseException):
    """Unexpected external effects must not disappear into best-effort handlers."""


def _deny_external(*_args, **_kwargs):
    raise ExternalEffectDenied("unexpected network or subprocess effect in constructor regression")


@pytest.fixture
def offline_runtime(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in tuple(os.environ):
        if name.endswith(("_API_KEY", "_TOKEN")) or name in {"OPENAI_BASE_URL", "OPENROUTER_BASE_URL"}:
            monkeypatch.delenv(name)
    for name, value in {"HOME": home, "USERPROFILE": home, "HERMES_HOME": hermes_home,
                        "HERMES_TEST_ISOLATION": hermes_home, "HERMES_DISABLE_LAZY_INSTALLS": "1"}.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("SYNTHETIC_FALLBACK_API_KEY", "synthetic-fallback-key")
    (hermes_home / "config.yaml").write_text(
        "model:\n  default: gpt-4.1-mini\n  provider: openai-api\n  context_length: 131072\n  streaming: false\n"
        "memory:\n  enabled: false\ncompression:\n  enabled: false\nagent:\n  environment_probe: false\n"
        "auxiliary:\n  title_generation:\n    enabled: false\n    model_upgrade_enabled: false\n"
        "fallback_providers:\n  - provider: deepseek\n    model: deepseek-flash\n"
        "    base_url: https://fallback.invalid/v1\n    key_env: SYNTHETIC_FALLBACK_API_KEY\n",
        encoding="utf-8",
    )
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, _deny_external)
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, _deny_external)

    # Resolve real stdlib SDK platform headers once before denying child processes.
    platform.platform()

    class DeniedPopen(subprocess.Popen):
        def __init__(self, *_args, **_kwargs):
            _deny_external()

    monkeypatch.setattr(subprocess, "Popen", DeniedPopen)

    for name in ("system", "popen", "execv", "execve", "execvp", "execvpe", "posix_spawn", "posix_spawnp", "fork"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _deny_external)

    import httpx

    requests = []

    def reply(_transport, request):
        if request.method == "GET" and str(request.url) in {
            "https://fallback.invalid/v1/models", "https://api.openai.com/v1/models",
        }:
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": "deepseek-flash", "object": "model", "context_length": 131072},
                {"id": "gpt-4.1-mini", "object": "model", "context_length": 131072},
            ]}, request=request)
        if request.method != "POST" or str(request.url) not in {
            "https://fallback.invalid/v1/chat/completions", "https://api.openai.com/v1/chat/completions",
        }:
            raise ExternalEffectDenied(f"unexpected synthetic wire destination: {request.method} {request.url}")
        body = json.loads(request.content)
        assert body.get("stream") is not True
        requests.append({"url": str(request.url), "authorization": request.headers["authorization"],
                         "model": body["model"], "messages": body["messages"]})
        return httpx.Response(200, json={
            "id": "synthetic-completion", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Synthetic response."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
        }, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reply)
    return hermes_home, requests


@pytest.mark.real_retry_backoff
@pytest.mark.parametrize("primary_available", [False, True], ids=["unavailable-primary", "resolved-primary"])
def test_real_constructor_and_next_turn_preserve_runtime_identity(offline_runtime, monkeypatch, primary_available):
    home, requests = offline_runtime
    if primary_available:
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-primary-key")
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.fallback_config import get_fallback_chain
    from hermes_constants import get_hermes_home
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from agent.secret_scope import get_secret
    from hermes_cli.fallback_config import resolve_entry_api_key
    from hermes_cli.auth import PROVIDER_REGISTRY

    assert get_secret("SYNTHETIC_FALLBACK_API_KEY") == "synthetic-fallback-key"
    assert "openai-api" in PROVIDER_REGISTRY and "deepseek" in PROVIDER_REGISTRY
    assert get_hermes_home() == home
    fallback = get_fallback_chain(load_user_config_effective())
    assert resolve_entry_api_key(fallback[0]) == "synthetic-fallback-key"
    if primary_available:
        assert get_secret("OPENAI_API_KEY") == "synthetic-primary-key"
    database = SessionDB(db_path=home / "state.db")
    agent = None
    try:
        agent = AIAgent(session_db=database, provider="openai-api", model="gpt-4.1-mini", fallback_model=fallback,
                        api_mode="chat_completions" if primary_available else "codex_responses",
                    enabled_toolsets=[], skip_memory=True, skip_context_files=True,
                    skip_background_review=True, quiet_mode=True, max_iterations=1,
                    save_trajectories=False, checkpoints_enabled=False, cwd=str(home))
        assert not requests, "constructor unexpectedly dispatched a model request"
        if primary_available:
            assert agent._primary_runtime["provider"] == "openai-api"
            assert agent._primary_runtime["api_key"] == "synthetic-primary-key"
            assert agent._try_activate_fallback(reason="synthetic primary refusal") is True
        else:
            assert agent._fallback_activated is True
            assert agent._primary_runtime is None
        assert (agent.provider, agent.model, str(agent.base_url).rstrip("/"), agent.api_key) == (
            "deepseek", "deepseek-flash", "https://fallback.invalid/v1", "synthetic-fallback-key")
        assert agent.api_mode == "chat_completions"
        active_client = agent.client
        history = None
        for text in ("first synthetic turn", "next synthetic turn"):
            result = agent.run_conversation(text, system_message="Answer briefly.", conversation_history=history)
            assert result.get("failed") is not True, result
            assert result["final_response"] == "Synthetic response."
            history = result["messages"]
            if not primary_available:
                assert agent._primary_runtime is None
                assert agent.client is active_client
                assert agent._fallback_activated is True
        expected = ("https://api.openai.com/v1/chat/completions", "gpt-4.1-mini", "Bearer synthetic-primary-key") \
            if primary_available else ("https://fallback.invalid/v1/chat/completions", "deepseek-flash",
                                       "Bearer synthetic-fallback-key")
        assert [(r["url"], r["model"], r["authorization"]) for r in requests] == [expected, expected]
        if primary_available:
            assert agent._fallback_activated is False
            assert agent.provider == "openai-api"
        import run_agent
        import agent.agent_init as init_module
        import agent.primary_runtime_snapshot as snapshot_module
        root = Path(__file__).resolve().parents[2]
        for module in (run_agent, init_module, snapshot_module):
            assert Path(module.__file__).resolve().is_relative_to(root)
    finally:
        if agent is not None:
            agent.close()
        database.close()
