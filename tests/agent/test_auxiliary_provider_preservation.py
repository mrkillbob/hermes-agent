"""Named auxiliary provider identity survives an explicit endpoint."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip provider env vars so each test starts clean."""
    for key in (
        "OPENROUTER_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY",
        "OPENAI_MODEL", "LLM_MODEL", "NOUS_INFERENCE_BASE_URL",
        "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
        "NVIDIA_API_KEY", "NVIDIA_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    # Module-level unhealthy cache (10-min TTL) leaks between tests;
    # earlier tests that call _mark_provider_unhealthy() poison the
    # cache for later ones, causing _resolve_auto_route to skip providers
    # that the test patched to return valid clients.
    import agent.auxiliary_client as _aux_mod
    _aux_mod._aux_unhealthy_until.clear()
    _aux_mod._aux_unhealthy_logged_at.clear()
    yield
    _aux_mod._aux_unhealthy_until.clear()
    _aux_mod._aux_unhealthy_logged_at.clear()


# ---------------------------------------------------------------------------
# Regression coverage for #76602 — auxiliary vision with a custom provider
# defined in the ``providers:`` section of config.yaml plus an explicit
# ``base_url`` was being silently downgraded to ``"custom"`` because
# ``_preserve_provider_with_base_url`` only consulted the built-in
# provider registry (``hermes_cli.providers.get_provider``). The
# downgrade routed the call through the bare-custom branch in
# ``resolve_provider_client`` with no key, producing 401s from
# auth-required providers (e.g. agnes-ai.cn, nvidia-nim with key_env).
# The fix also checks ``_get_named_custom_provider`` so a named
# user-defined provider + explicit base_url stays named through to the
# named-custom-provider key-resolution branch.
# ---------------------------------------------------------------------------


class TestPreserveNamedCustomProviderWithBaseUrl:
    """#76602 — _resolve_task_provider_model must keep a user-defined
    provider named when the call site passes ``provider=<name>`` +
    ``base_url=...`` (the shape async_call_llm takes after resolving the
    auxiliary.vision task config). The bare-custom downgrade to the
    ``"custom"`` string loses the key and produces 401s.
    """

    def test_user_defined_provider_named_in_config_is_preserved(self, monkeypatch):
        """A provider name from ``providers:`` survives explicit base_url.

        Before the fix this returned ``("custom", ...)`` → bare-custom
        branch in ``resolve_provider_client`` → ``no-key-required`` → 401.
        After the fix it returns ``("agnes-ai.cn", ...)`` so the
        named-custom-provider branch picks up
        ``providers.<name>.api_key`` (or the configured ``key_env``).
        """
        import agent.auxiliary_client as ac

        fake_entry = {
            "name": "AgnesAI",
            "base_url": "https://api.agnes-ai.cn/v1",
            "api_key": "sk-agnes-test",
            "model": "agnes-2.5-flash",
        }
        with patch(
            "hermes_cli.runtime_provider._get_named_custom_provider",
            return_value=fake_entry,
        ), patch(
            "hermes_cli.providers.get_provider", return_value=None,
        ):
            resolved_provider, _model, base_url, _api_key, _api_mode = (
                ac._resolve_task_provider_model(
                    task="vision",
                    provider="agnes-ai.cn",
                    model="agnes-2.5-flash",
                    base_url="https://api.agnes-ai.cn/v1",
                    api_key=None,
                )
            )

        assert resolved_provider == "agnes-ai.cn", (
            "User-defined provider from providers: section must be preserved "
            "instead of being downgraded to 'custom' (issue #76602)"
        )
        assert base_url == "https://api.agnes-ai.cn/v1"

    def test_built_in_provider_with_base_url_still_preserved(self, monkeypatch):
        """Built-in registry hit still wins — user-defined fallback only
        fires when the built-in registry missed. This guards against
        regressing the pre-existing built-in provider behavior.
        """
        import agent.auxiliary_client as ac

        with patch(
            "hermes_cli.providers.get_provider",
            return_value={"name": "Anthropic", "api_key": "sk-anthropic"},
        ):
            resolved_provider, _model, base_url, _api_key, _api_mode = (
                ac._resolve_task_provider_model(
                    task="moa_reference",
                    provider="anthropic",
                    model="claude-sonnet-4-6",
                    base_url="https://api.anthropic.com/v1",
                    api_key="sk-anthropic",
                )
            )

        assert resolved_provider == "anthropic"
        assert base_url == "https://api.anthropic.com/v1"

    def test_unknown_provider_with_base_url_falls_back_to_custom_downgrade(self, monkeypatch):
        """Provider name not in either registry → keep the existing
        ``"custom"`` downgrade behavior. Nothing changes for truly
        anonymous custom endpoints (no name → no key → bare-custom branch
        handles ``no-key-required`` itself).
        """
        import agent.auxiliary_client as ac

        with patch(
            "hermes_cli.runtime_provider._get_named_custom_provider",
            return_value=None,
        ), patch(
            "hermes_cli.providers.get_provider", return_value=None,
        ):
            resolved_provider, _model, base_url, _api_key, _api_mode = (
                ac._resolve_task_provider_model(
                    task="vision",
                    provider="some-unknown-gateway",
                    model="custom-model",
                    base_url="https://example.com/v1",
                    api_key="some-token",
                )
            )

        # Pre-existing behavior: unknown name + explicit base_url →
        # downgrade to "custom" so the caller routes through the
        # bare-custom branch (which uses the explicit api_key).
        assert resolved_provider == "custom"
        assert base_url == "https://example.com/v1"

    def test_hardcoded_allowlist_still_works_when_both_registries_unavailable(self, monkeypatch):
        """If both the built-in registry and the user-defined config are
        unavailable (e.g. early import path before config is loaded), the
        existing hardcoded allowlist still returns True for known names —
        so xai-oauth / qwen-oauth / etc. aren't regressed by the fix.
        """
        import agent.auxiliary_client as ac

        def _boom(_name):
            raise RuntimeError("catalog unavailable")

        with patch(
            "hermes_cli.runtime_provider._get_named_custom_provider",
            side_effect=_boom,
        ), patch(
            "hermes_cli.providers.get_provider", side_effect=_boom,
        ):
            resolved_provider, _model, _base_url, _api_key, _api_mode = (
                ac._resolve_task_provider_model(
                    task="moa_reference",
                    provider="xai-oauth",
                    model="grok-3",
                    base_url="https://api.x.ai/v1",
                    api_key="xai-token",
                )
            )

        assert resolved_provider == "xai-oauth"
