"""Metadata scope preserves local/managed precedence without shared credential discovery."""

import pytest

from agent import secret_scope as ss


@pytest.mark.parametrize("include_shared", [False, True])
def test_metadata_scope_omits_shared_builders_only(tmp_path, monkeypatch, include_shared):
    from hermes_cli import env_loader, managed_scope

    (tmp_path / ".env").write_text("LOCAL=own\nCOLLISION=own\n", encoding="utf-8")
    calls = []

    def shared(label):
        def build(home):
            assert include_shared, "metadata must not inspect shared credential stores"
            assert home == tmp_path
            calls.append(label)
            return {label: "synthetic"}
        return build

    monkeypatch.setattr(ss, "_shared_model_provider_secrets", shared("MODEL"))
    monkeypatch.setattr(ss, "_shared_github_automation_secrets", shared("GITHUB"))
    monkeypatch.setattr(env_loader, "get_secret_source_values", lambda home: {
        "CACHED": "external", "COLLISION": "external", "TERMINAL_ENV": "ignored"})
    monkeypatch.setattr(env_loader, "hydrate_profile_secret_sources", lambda *a: pytest.fail("no hydration"))
    monkeypatch.setattr(managed_scope, "load_managed_env", lambda: {
        "COLLISION": "managed", "TERMINAL_ENV": "ignored"})
    result = (ss.build_profile_secret_scope(tmp_path) if include_shared else
              ss.build_profile_secret_scope(tmp_path, include_shared_credentials=False))
    expected = {"LOCAL": "own", "CACHED": "external", "COLLISION": "managed"}
    if include_shared:
        expected.update(MODEL="synthetic", GITHUB="synthetic")
    assert result == expected
    assert calls == (["MODEL", "GITHUB"] if include_shared else [])
