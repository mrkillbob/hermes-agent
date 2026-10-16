"""CommandCode keeps Hermes credentials compatible as catalog metadata evolves."""

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("catalog_present", [False, True])
@pytest.mark.parametrize(
    "name,canonical,transport,url_var",
    [
        ("commandcode", "commandcode", "openai_chat", "COMMANDCODE_BASE_URL"),
        ("commandcode-chat", "commandcode", "openai_chat", "COMMANDCODE_BASE_URL"),
        ("commandcode-anthropic", "commandcode-anthropic", "anthropic_messages", "COMMANDCODE_ANTHROPIC_BASE_URL"),
        ("commandcode-claude", "commandcode-anthropic", "anthropic_messages", "COMMANDCODE_ANTHROPIC_BASE_URL"),
    ],
)
def test_commandcode_credential_metadata_survives_catalog(
    monkeypatch, catalog_present, name, canonical, transport, url_var,
):
    from hermes_cli import providers

    catalog = SimpleNamespace(
        env=("CMD_API_KEY",), name="CommandCode", doc="https://commandcode.ai/docs/provider",
        api="https://api.commandcode.ai/provider/v1",
    )
    monkeypatch.setattr(
        providers, "_models_dev_info",
        lambda provider, allow_network=True: catalog if catalog_present and provider == "commandcode" else None,
    )

    result = providers.resolve_provider_full(name, {}, [])

    assert result is not None and result.id == canonical
    assert result.api_key_env_vars == ("COMMANDCODE_API_KEY", "CMD_API_KEY")
    assert result.transport == transport
    assert result.base_url == "https://api.commandcode.ai/provider/v1"
    assert result.base_url_env_var == url_var


@pytest.mark.parametrize(
    "provider,url_var",
    [("commandcode", "COMMANDCODE_BASE_URL"), ("commandcode-anthropic", "COMMANDCODE_ANTHROPIC_BASE_URL")],
)
@pytest.mark.parametrize(
    "legacy,current,expected,source",
    [
        ("test-legacy-key", "", "test-legacy-key", "COMMANDCODE_API_KEY"),
        (None, "test-current-key", "test-current-key", "CMD_API_KEY"),
        ("test-legacy-key", "test-current-key", "test-legacy-key", "COMMANDCODE_API_KEY"),
        ("", "test-fallback-key", "test-fallback-key", "CMD_API_KEY"),
    ],
)
def test_commandcode_auth_accepts_both_names_preserving_legacy_priority(
    monkeypatch, provider, url_var, legacy, current, expected, source,
):
    from hermes_cli import auth, config

    values = {
        "COMMANDCODE_API_KEY": legacy, "CMD_API_KEY": current,
        url_var: "http://commandcode.test.invalid/custom/v1",
    }
    monkeypatch.setattr(config, "get_env_value_prefer_dotenv", lambda name: values.get(name, ""))
    monkeypatch.setattr(auth, "_model_level_key_env", lambda provider: "")
    monkeypatch.setenv(url_var, values[url_var])

    result = auth.resolve_api_key_provider_credentials(provider)

    assert result["api_key"] == expected
    assert result["source"] == source
    assert result["base_url"] == values[url_var]


def test_catalog_absent_preserves_registered_commandcode_profile(monkeypatch):
    import providers as profiles
    from hermes_cli import providers
    from providers.base import ProviderProfile

    profile = ProviderProfile(
        name="commandcode", api_mode="anthropic_messages",
        env_vars=("PRIVATE_CMD_KEY", "PRIVATE_CMD_BASE_URL"),
        base_url="https://commandcode.test.invalid/private/v1",
    )
    monkeypatch.setattr(providers, "_models_dev_info", lambda provider, allow_network=True: None)
    monkeypatch.setattr(profiles, "get_provider_profile", lambda name: profile if name == "commandcode" else None)

    result = providers.resolve_provider_full("commandcode", {}, [])

    assert result is not None
    assert result.transport == "anthropic_messages"
    assert result.api_key_env_vars == ("PRIVATE_CMD_KEY",)
    assert result.base_url == profile.base_url
    assert result.base_url_env_var == "PRIVATE_CMD_BASE_URL"
