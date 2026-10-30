"""Bundled Copilot must never be sent through the external left-core plugin installer."""

from pathlib import Path

import pytest

import hermes_cli.left_core_migration as migration


@pytest.fixture
def copilot_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  provider: copilot\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "fixture-token")
    monkeypatch.setattr(migration, "_attempted", set())
    return home


def test_bundled_copilot_skips_external_migration_and_notice(copilot_home, monkeypatch):
    import hermes_cli.memory_provider_migration as catalog
    import hermes_cli.plugins_cmd as installer

    def unexpected(*args, **kwargs):
        pytest.fail("bundled Copilot reached the external catalog or installer")

    monkeypatch.setattr(catalog, "catalog_source", unexpected)
    monkeypatch.setattr(installer, "dashboard_install_plugin", unexpected)
    before = (copilot_home / "config.yaml").read_bytes()
    notices = []
    assert migration._pending(copilot_home, say=notices.append, process_env=True) == []
    assert migration.migrate_home(copilot_home, install=unexpected, say=notices.append, process_env=True) == []
    assert notices == []
    assert (copilot_home / "config.yaml").read_bytes() == before
    assert not (copilot_home / "plugins" / "github-copilot").exists()


def test_bundled_copilot_is_not_marked_left_core(copilot_home):
    config = {"model": {"provider": "copilot"}}
    feature = migration._provider_feature("copilot")
    assert feature is not None and migration._bundled_copilot_present(feature)
    assert not migration.record_core_use(copilot_home, config, feature.left_at)
    assert "_left_core_used" not in config


def test_unavailable_bundled_copilot_does_not_suggest_external_install(copilot_home):
    hint = migration.provider_install_hint("copilot")
    assert "hermes logs" in hint
    assert "plugins install" not in hint


def test_absent_bundle_retains_left_core_recovery(copilot_home, monkeypatch):
    import hermes_cli.memory_provider_migration as catalog

    monkeypatch.setattr(migration, "_bundled_copilot_present", lambda feature: False)
    monkeypatch.setattr(catalog, "catalog_source", lambda name: name)
    monkeypatch.setattr(migration, "feature_in_use", lambda feature, home, **kwargs: feature.plugin == "github-copilot")
    planned = migration._pending(copilot_home, say=lambda message: None, process_env=True)
    assert [feature.plugin for feature in planned] == ["github-copilot"]
