"""Metadata callers retain the canonical launch/secondary scope and hold semantics."""

import pytest

from agent import secret_scope
from hermes_cli import startup_hold
from tui_gateway import launch_profile_policy as policy


@pytest.mark.parametrize("launch", [False, True])
@pytest.mark.parametrize("include_shared", [False, True])
@pytest.mark.parametrize("active", [False, True])
def test_owner_metadata_scope_preserves_launch_precedence(tmp_path, monkeypatch, launch, include_shared, active):
    calls = []

    def build(home, **options):
        calls.append((home, options))
        return {"COLLISION": "file", "LOCAL": "own"}

    monkeypatch.setattr(startup_hold, "active", lambda: False)
    monkeypatch.setattr(secret_scope, "_is_process_home", lambda home: launch)
    monkeypatch.setattr(secret_scope, "build_profile_secret_scope", build)
    monkeypatch.setattr(policy, "_snapshot", {"ENV_ONLY": "launch", "COLLISION": "launch"})
    monkeypatch.setenv("ENV_ONLY", "launch")
    monkeypatch.setenv("COLLISION", "launch")
    token = secret_scope.set_multiplex_context(active)
    try:
        result = (policy.owner_secret_scope(tmp_path) if include_shared else
                  policy.owner_secret_scope(tmp_path, include_shared_credentials=False))
    finally:
        secret_scope.reset_multiplex_context(token)
    expected = {"COLLISION": "file", "LOCAL": "own"}
    if launch:
        expected["ENV_ONLY"] = "launch"
    for key, value in expected.items():
        assert result[key] == value
    if not launch:
        assert "ENV_ONLY" not in result
    assert calls == [(tmp_path, {} if include_shared else {"include_shared_credentials": False})]


def test_metadata_scope_does_not_bypass_startup_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(startup_hold, "active", lambda: True)
    monkeypatch.setattr(secret_scope, "build_profile_secret_scope", lambda *a, **k: pytest.fail("held"))
    assert policy.owner_secret_scope(tmp_path, include_shared_credentials=False) == {}
