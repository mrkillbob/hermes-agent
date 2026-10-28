"""GitHub Copilot ACP setup and Accounts-card surfaces: the ``hermes model`` flow and the
Desktop card that points at the Copilot CLI's own login. Reached through ``SETUP_FIELDS``."""

from __future__ import annotations

import contextlib
from typing import Any


def account_status() -> dict[str, Any]:
    """Status for copilot-acp. ``logged_in`` only on positive evidence (env token or known on-disk
    store); the CLI may hold its session in an OS keychain Hermes can't read, so the unverified
    state reads "managed by the Copilot CLI" — never signed out."""
    try:
        from hermes_cli.auth import get_external_process_provider_status
        status = get_external_process_provider_status("copilot-acp") or {}
    except Exception:
        status = {}
    verified = bool(status.get("auth_verified"))
    configured = bool(status.get("configured"))
    if verified:
        source_label = status.get("auth_source") or "Copilot credentials detected"
    elif configured:
        found = status.get("resolved_command") or status.get("command") or "copilot"
        source_label = f"Managed by the GitHub Copilot CLI ({found})"
    else:
        source_label = "GitHub Copilot CLI not found on PATH"
    return {
        "logged_in": verified, "source": "copilot_cli", "source_label": source_label, "token_preview": None,
        "expires_at": None, "has_refresh_token": False, "configured": configured,
    }


def model_flow_copilot_acp(config, current_model="", args=None):
    """GitHub Copilot ACP flow using the local Copilot CLI."""
    from hermes_cli.auth import (
        PROVIDER_REGISTRY, get_external_process_provider_status, resolve_api_key_provider_credentials,
        resolve_external_process_provider_credentials)
    from hermes_cli.model_setup_flows_common import _finish_model, _pick_model_or_prompt, _say
    from providers import get_provider_profile

    del config, args
    provider_id = "copilot-acp"
    pconfig = PROVIDER_REGISTRY[provider_id]
    status = get_external_process_provider_status(provider_id)
    resolved_command = status.get("resolved_command") or status.get("command") or "copilot"
    effective_base = status.get("base_url") or pconfig.inference_base_url

    _say("  GitHub Copilot ACP delegates Hermes turns to `copilot --acp`.",
         "  Hermes currently starts its own ACP subprocess for each request.",
         "  Hermes uses your selected model as a hint for the Copilot ACP session.",
         f"  Command: {resolved_command}", f"  Backend marker: {effective_base}", "")
    try:
        creds = resolve_external_process_provider_credentials(provider_id)
    except Exception as exc:  # health: allow BLE001 -- shown to the user, then the flow stops (moved verbatim)
        _say(f"  ⚠ {exc}", "  Set HERMES_COPILOT_ACP_COMMAND or COPILOT_CLI_PATH if Copilot CLI is installed elsewhere.")
        return
    effective_base = creds.get("base_url") or effective_base

    catalog_api_key = ""
    with contextlib.suppress(Exception):
        catalog_api_key = resolve_api_key_provider_credentials("copilot").get("api_key", "")
    # The GitHub catalog helpers belong to the sibling ``copilot`` profile (one plugin in the labs repo).
    copilot_setup = _sibling_setup_module(get_provider_profile("copilot"))
    _catalog, catalog_ids, _normalize = copilot_setup._copilot_catalog(catalog_api_key)
    selected = _pick_model_or_prompt(
        copilot_setup._copilot_model_list(catalog_ids), "Model name: ", current_model=_normalize(current_model),
        confirm_provider=provider_id, confirm_base_url=effective_base, confirm_api_key=catalog_api_key)
    if selected:
        selected = _normalize(selected)
    _finish_model(selected, provider_id, f"Default model set to: {selected} (via {pconfig.name})",
                  base_url=effective_base, api_mode="chat_completions")


def _sibling_setup_module(profile) -> Any:
    """The ``setup`` module next to *profile*'s plugin package (it registered under its own import name)."""
    import importlib
    return importlib.import_module(f"{type(profile).__module__}.setup")


SETUP_FIELDS: dict[str, Any] = {
    "model_setup_flow": model_flow_copilot_acp,
    # `copilot login` is the non-interactive subcommand; `copilot /login` is not valid
    # (slash-commands only exist inside an interactive session).
    "account_card": {"name": "GitHub Copilot (ACP)", "flow": "external", "cli_command": "copilot login",
                     "docs_url": "https://docs.github.com/en/copilot"},
    "account_status": account_status,
}
