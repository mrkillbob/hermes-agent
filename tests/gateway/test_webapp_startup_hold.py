"""Webapp additions must preserve the targetless native startup hold."""
import asyncio
import json
from types import SimpleNamespace

import pytest


def _refuse_effect(*args, **kwargs):
    pytest.fail("startup hold reached Webapp configuration or credential work")


def _ask(server, verb):
    request = json.dumps({"protocol": 1, "verb": verb, "id": 1, "params": {}}).encode()
    return json.loads(server.handle_request_line(request, "same-user-peer"))


def test_held_runtime_uses_only_the_private_native_listener(monkeypatch, tmp_path):
    from gateway import run_api, run_runtime, webapp_listener
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(webapp_listener, "listener_plan", _refuse_effect)
    monkeypatch.setattr(webapp_listener, "browser_session_token", _refuse_effect)
    monkeypatch.setattr(run_runtime, "_authorities", _refuse_effect)
    calls = []

    async def start(runner, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(api_origin="http://127.0.0.1:50001")

    monkeypatch.setattr(run_api, "start_gateway_api", start)
    runner = SimpleNamespace(_startup_hold=True, session_runtime_descriptor={})
    asyncio.run(run_runtime.start_gateway_runtime_api(runner))
    assert calls == [{}]  # native listener defaults are loopback + ephemeral, without a Webapp mount
    assert runner.session_runtime_descriptor["api_origin"] == "http://127.0.0.1:50001"
    assert runner.webapp_status == {"state": "unavailable", "url": None, "reason": "startup_hold"}


@pytest.mark.parametrize("state", ["starting", "off", "serving"])
@pytest.mark.parametrize("verb", ["webapp-status", "webapp-launch", "webapp-sign-out"])
def test_held_private_webapp_verbs_refuse_before_any_effect(monkeypatch, tmp_path, state, verb):
    from gateway import webapp_listener
    from gateway.control_socket import GatewayControlServer
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(webapp_listener, "browser_session_token", _refuse_effect)
    monkeypatch.setattr(webapp_listener, "_bundle_missing", _refuse_effect)
    server = GatewayControlServer(tmp_path)
    runner = SimpleNamespace(_startup_hold=True, session_control_server=server,
                             webapp_status={"state": state, "url": None, "reason": None})
    webapp_listener.register_control_verbs(runner, tmp_path)
    answer = _ask(server, verb)
    assert answer["ok"] is False and "startup_hold" in answer["error"], answer


def test_held_bootstrap_webapp_verbs_refuse_from_the_first_control_request(monkeypatch, tmp_path):
    from gateway import control_socket, run_bootstrap
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    answers = []

    async def start(server):
        answers.extend(_ask(server, verb) for verb in ("webapp-status", "webapp-launch", "webapp-sign-out"))
        return False

    monkeypatch.setattr(control_socket.GatewayControlServer, "start", start)
    runner = SimpleNamespace(_startup_hold=True, session_runtime_descriptor={}, session_ticket_store=None)
    assert asyncio.run(run_bootstrap._start_gateway_start_control_socket(runner)) is None
    assert len(answers) == 3
    assert all(answer["ok"] is False and "startup_hold" in answer["error"] for answer in answers)
