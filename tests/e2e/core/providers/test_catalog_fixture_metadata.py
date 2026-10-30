"""Catalog routing fixtures must not cold-fetch another provider's picker metadata."""
from hermes_cli import model_catalog, models
from tests.e2e.core.providers._catalog_helpers import Row, TurnResult, evaluate, write_home


def test_catalog_home_supplies_picker_metadata_without_foreign_network(tmp_path, monkeypatch):
    home = write_home(tmp_path, {"provider": "custom", "base_url": "http://127.0.0.1:9/v1"})
    monkeypatch.setattr(models, "_openrouter_catalog_cache", None)
    monkeypatch.setattr(models, "_openrouter_catalog_disk_path",
                        lambda: home / ".hermes" / "cache" / "openrouter_curated_catalog.json")
    monkeypatch.setattr(model_catalog, "get_curated_openrouter_models", lambda: None)

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("Routing fixture attempted an unrelated live model-catalog fetch")

    monkeypatch.setattr(models, "_fetch_live_catalog_index", unexpected_network)
    assert models.fetch_openrouter_models()


def test_catalog_still_rejects_foreign_provider_egress():
    row = Row("chosen", "chat_completions", "api_key", "CHOSEN_KEY", "https://chosen.invalid/v1", (), True)
    other = Row("other", "chat_completions", "api_key", "OTHER_KEY", "https://other.invalid/v1", (), True)
    turn = TurnResult(row, 0, "", "", [], ["other.invalid:443"], "fake-key", {"fake-key"}, None, "canary", 0)
    assert not evaluate(turn, [row, other])["no_egress_to_foreign_provider_hosts"]
