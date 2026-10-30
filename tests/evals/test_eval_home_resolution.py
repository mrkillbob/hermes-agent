"""The eval scripts read credentials from the canonical Hermes home, not a hardcoded ``~/.hermes``.

Regression for the sync of upstream's ``evals/prefix_rediet`` and ``evals/reasoning_replay``.
``HERMES_DATA_DIR_SUFFIX`` moves the platform default, so a hardcoded path disagrees with it on every host.
"""
from __future__ import annotations

import importlib
import json

import pytest

from hermes_constants import get_hermes_home


@pytest.fixture
def default_home(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_DATA_DIR_SUFFIX", "-evalcheck")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    home = get_hermes_home()
    home.mkdir(parents=True)
    return home


def test_prefix_rediet_reads_the_nous_grant_from_the_canonical_home(default_home, monkeypatch):
    monkeypatch.delenv("PRD_INFERENCE_KEY", raising=False)
    (default_home / "auth.json").write_text(json.dumps(
        {"providers": {"nous": {"inference_base_url": "https://example.test/v1", "agent_key": "k-canonical"}}}))
    run = importlib.import_module("evals.prefix_rediet.run")
    env = run._inference_env()
    assert (env["PRD_INFERENCE_URL"], env["PRD_INFERENCE_KEY"]) == ("https://example.test/v1", "k-canonical")


def test_reasoning_replay_credentials_live_in_the_canonical_home(default_home):
    harness = importlib.reload(importlib.import_module("evals.reasoning_replay.harness"))
    assert harness.AUTH_JSON == default_home / "auth.json"
    assert harness.DOTENV == default_home / ".env"
