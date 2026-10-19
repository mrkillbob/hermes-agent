"""The dashboard's dispatch nudge honors the configured kanban caps, like the gateway loop and CLI.

A bare ``dispatch_once`` has no host cap, no per-profile cap and no ``default_assignee``; a UI click
could start more workers than the operator allows.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as kbd


def _router():
    plugin = Path(__file__).resolve().parents[2] / "plugins/kanban/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_kanban_limits_test", plugin)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.router


@pytest.fixture
def client(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    app = FastAPI()
    app.include_router(_router(), prefix="/api/plugins/kanban")
    return TestClient(app)


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake(conn, **kwargs):
        calls.append(kwargs)
        return kbd.DispatchResult()

    monkeypatch.setattr(kbd, "dispatch_once", fake)
    return calls


def _config(monkeypatch, kanban):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"kanban": kanban})


def test_nudge_passes_configured_caps_and_only_tightens_max_spawn(client, captured, monkeypatch):
    _config(monkeypatch, {
        "max_in_progress": 3, "max_spawn": 2, "default_assignee": "worker",
        "max_in_progress_per_profile": 1, "max_in_progress_by_model": {"a/b": 1},
        "priority_runtime_guard": {"enabled": False},
    })
    assert client.post("/api/plugins/kanban/dispatch?max=8").status_code == 200
    assert client.post("/api/plugins/kanban/dispatch?max=1").status_code == 200
    wide, narrow = captured
    assert wide["max_spawn"] == 2 and narrow["max_spawn"] == 1
    for call in captured:
        assert call["max_in_progress"] == 3
        assert call["max_in_progress_per_profile"] == 1
        assert call["max_in_progress_by_model"] == {"a/b": 1}
        assert call["default_assignee"] == "worker"


def test_nudge_fails_closed_when_config_is_unreadable(client, captured, monkeypatch):
    def broken():
        raise OSError("unreadable")

    monkeypatch.setattr("hermes_cli.config.load_config", broken)
    response = client.post("/api/plugins/kanban/dispatch")
    assert response.status_code == 503
    assert captured == []


@pytest.mark.parametrize("text", ["kanban: [unterminated\n", "kanban: []\n", "kanban: null\n",
                                  "kanban:\n  max_in_progress: invalid\n"])
def test_nudge_refuses_real_malformed_config_without_dispatch(client, captured, text):
    from hermes_cli.config import get_config_path

    get_config_path().write_text(text)
    response = client.post("/api/plugins/kanban/dispatch")
    assert response.status_code == 503
    assert captured == []


def test_nudge_refuses_last_known_good_fallback(client, captured):
    from hermes_cli.config import get_config_path, load_config

    get_config_path().write_text("kanban:\n  max_in_progress: 2\n")
    assert load_config()["kanban"]["max_in_progress"] == 2
    get_config_path().write_text("kanban: [unterminated\n")
    response = client.post("/api/plugins/kanban/dispatch")
    assert response.status_code == 503
    assert captured == []
