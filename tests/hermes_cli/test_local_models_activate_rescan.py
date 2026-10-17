"""Activation requires a live model listing; staging cannot bounce a shared router."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_activate_requires_explicit_restart_when_shared_listing_lacks_model(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    from hermes_cli.web_routers import local_models as lm

    staged = tmp_path / "models"
    staged.mkdir(parents=True)
    (staged / "Qwen3-4B-Q4_K_M.gguf").write_bytes(b"GGUF\x00\x00\x00\x00")

    calls = {"refresh": 0, "known": [{"id": "some-other-model"}]}

    def fake_refresh():
        calls["refresh"] += 1
        calls["known"] = [{"id": "Qwen3-4B-Q4_K_M"}]
        return True

    def fake_router_request(endpoint, path, *, timeout, payload=None):
        assert path == "/models"
        return {"data": calls["known"]}

    job = lm._job("model-activate", "Qwen3-4B-Q4_K_M", model_id="Qwen3-4B-Q4_K_M")
    with (
        patch.object(lm.bootstrap, "ensure_local_runtime", return_value=None),
        patch.object(
            lm,
            "_state_endpoint",
            return_value={"base_url": "http://127.0.0.1:18434/v1", "api_key": "k"},
        ),
        patch.object(lm.bootstrap, "refresh_local_runtime", fake_refresh),
        patch.object(lm, "_router_request", fake_router_request),
        patch.object(lm, "_set_runtime_enabled", lambda v: {}),
        patch(
            "hermes_cli.web_server_config._apply_model_assignment_sync",
            lambda *a, **k: {"ok": True},
        ),
    ):
        with pytest.raises(RuntimeError, match='Stop/start the local server'):
            lm._ensure_server(
                job,
                {"local_runtime": {"enabled": True}},
                "Qwen3-4B-Q4_K_M",
                fail_detail="server failed",
                skip_msg="skipped",
            )

    assert calls["refresh"] == 0
    assert calls['known'] == [{'id': 'some-other-model'}]


def test_activate_skips_rescan_when_owned_server_already_lists_the_model(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    from hermes_cli.web_routers import local_models as lm

    calls = {"refresh": 0}

    class FakeSup:
        def models(self):
            return {"Qwen3-4B-Q4_K_M": "loaded"}

    job = lm._job("model-activate", "Qwen3-4B-Q4_K_M", model_id="Qwen3-4B-Q4_K_M")
    with (
        patch.object(lm.bootstrap, "ensure_local_runtime", return_value=FakeSup()),
        patch.object(
            lm.bootstrap,
            "refresh_local_runtime",
            lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
        ),
    ):
        lm._ensure_server(
            job,
            {"local_runtime": {"enabled": True}},
            "Qwen3-4B-Q4_K_M",
            fail_detail="server failed",
            skip_msg="skipped",
        )

    assert calls["refresh"] == 0, "no bounce when the listing already knows the model"


@pytest.mark.asyncio
async def test_deferred_refresh_does_not_assign_an_unserved_model(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from starlette.requests import Request
    from hermes_cli.web_routers import local_models as lm
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    config = home / 'config.yaml'
    before = 'model:\n  provider: cloud-fixture\n  default: existing-choice\n'
    config.write_text(before)
    writes = []
    monkeypatch.setattr(lm.bootstrap, 'staged_model_ids', lambda: ['fixture'])
    monkeypatch.setattr(lm.bootstrap, 'ensure_local_runtime', lambda *args, **kwargs: None)
    monkeypatch.setattr(lm, '_state_endpoint', lambda: {'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'fixture'})
    monkeypatch.setattr(lm, '_router_request', lambda *args, **kwargs: {'data': [{'id': 'other'}]})
    monkeypatch.setattr(lm.bootstrap, 'refresh_local_runtime', lambda: False)
    monkeypatch.setattr(lm, '_set_runtime_enabled', lambda value: writes.append(('enabled', value)))
    monkeypatch.setattr(lm, '_assign_default', lambda *args: writes.append(('assigned',)))
    monkeypatch.setattr(lm, '_spawn_job', lambda job, name, run, **kwargs: run())
    with pytest.raises(HTTPException, match='Stop/start the local server') as error:
        await lm.local_models_activate(lm.ModelActivateBody(model_id='fixture'),
                                       Request({'type': 'http', 'app': FastAPI()}))
    assert error.value.status_code == 409
    assert writes == []
    assert config.read_text() == before
