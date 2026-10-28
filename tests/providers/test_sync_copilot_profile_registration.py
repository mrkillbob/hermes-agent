"""Both first-party Copilot profiles register on the current provider base."""

def test_bundled_copilot_profiles_register_and_acp_evidence_is_profile_owned(monkeypatch):
    from providers import get_provider_profile
    api = get_provider_profile("copilot")
    acp = get_provider_profile("copilot-acp")
    from plugins.model_providers.copilot import credentials

    monkeypatch.setattr(credentials, "copilot_cli_auth_evidence", lambda: (True, "fixture-cli"))
    assert api is not None and api.auth_type == "copilot"
    assert acp is not None and acp.auth_type == "external_process"
    assert acp.process_command == "copilot"
    assert acp.process_command_env_vars == ("HERMES_COPILOT_ACP_COMMAND", "COPILOT_CLI_PATH")
    assert acp.external_auth_evidence() == (True, "fixture-cli")


def test_copilot_setup_falls_back_to_registered_profile_catalog(capsys):
    from plugins.model_providers.copilot.setup import _copilot_model_list
    from providers import get_provider_profile

    profile = get_provider_profile("copilot")
    assert profile is not None and profile.static_models
    assert _copilot_model_list([]) == list(profile.static_models)
    assert "showing defaults" in capsys.readouterr().out
