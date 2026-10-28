"""GitHub Copilot setup and sign-in surfaces: the ``hermes model`` flow, the setup effort picker,
and the Desktop Accounts card with its in-app GitHub device-code login.

Core reaches these only through the generic ``ProviderProfile`` setup fields (``SETUP_FIELDS``).
"""

from __future__ import annotations

import contextlib
from typing import Any

# Device-code endpoints live on the GitHub web host; tests point this at a local fake.
_DEVICE_LOGIN_BASE = "https://github.com"
_DEVICE_VERIFICATION_URL = "https://github.com/login/device"
_DEVICE_CODE_EXPIRES_IN = 900   # GitHub's documented device-code lifetime when the reply omits it
_TOKEN_ENV_VAR = "COPILOT_GITHUB_TOKEN"
_DEVICE_ERRORS = {"expired_token": ("expired", "Device code expired. Please try again."),
                  "access_denied": ("denied", "Authorization was denied.")}


def _copilot_model_list(live_ids) -> list:
    """Live GitHub Copilot ids, or the curated fallback with a warning."""
    from hermes_cli.model_setup_flows_common import _say
    from hermes_cli.models import _PROVIDER_MODELS
    if live_ids:
        model_list = [model_id for model_id in live_ids if model_id]
        print(f"  Found {len(model_list)} model(s) from GitHub Copilot")
        return model_list
    model_list = _PROVIDER_MODELS.get("copilot", [])
    if model_list:
        _say("  ⚠ Could not auto-detect models from GitHub Copilot — showing defaults.",
             '    Use "Enter custom model name" if you do not see your model.')
    return model_list


def _copilot_catalog(api_key: str):
    """``(catalog, catalog_ids, normalize)`` for a GitHub token; *normalize* canonicalizes a
    model id against the catalog (identity when unknown)."""
    from .catalog import fetch_github_model_catalog, normalize_copilot_model_id
    catalog = fetch_github_model_catalog(api_key)
    ids = [item.get("id", "") for item in catalog if item.get("id")] if catalog else []

    def _normalize(mid):
        return normalize_copilot_model_id(mid, catalog=catalog, api_key=api_key) or mid

    return catalog, ids, _normalize


def _copilot_obtain_token() -> bool:
    """No Copilot token yet: offer device-code login or manual entry. False = stop."""
    from hermes_cli.config import save_env_value
    from hermes_cli.model_setup_flows_common import _ask, _note_setup_failure, _say
    _say("No GitHub token configured for GitHub Copilot.", "", "  Supported token types:",
         "    → OAuth token (gho_*)          via `copilot login` or device code flow",
         "    → Fine-grained PAT (github_pat_*)  with Copilot Requests permission",
         "    → GitHub App token (ghu_*)     via environment variable",
         "    ✗ Classic PAT (ghp_*)          NOT supported by Copilot API", "", "  Options:",
         "    1. Login with GitHub (OAuth device code flow)", "    2. Enter a token manually", "    3. Cancel", "")
    choice = _ask("  Choice [1-3]: ", raw=True, cancel_msg="")
    if choice is None:
        return False
    if choice == "1":
        try:
            from .credentials import copilot_device_code_login
            token = copilot_device_code_login()
            if not token:
                print("  Login cancelled or failed.")
                return False
            save_env_value(_TOKEN_ENV_VAR, token)
            _say("  Copilot token saved.", "")
        except Exception as exc:
            print(f"  Login failed: {exc}")
            _note_setup_failure("auth")
            return False
        return True
    if choice == "2":
        new_key = _ask("  Token (COPILOT_GITHUB_TOKEN): ", secret=True, cancel_msg="")
        if new_key is None:
            return False
        if not new_key:
            print("  Cancelled.")
            return False
        # Validate token type
        with contextlib.suppress(ImportError):
            from .credentials import validate_copilot_token
            valid, msg = validate_copilot_token(new_key)
            if not valid:
                print(f"  ✗ {msg}")
                return False
        save_env_value(_TOKEN_ENV_VAR, new_key)
        _say("  Token saved.", "")
        return True
    print("  Cancelled.")
    return False


def model_flow_copilot(config, current_model="", args=None):
    """GitHub Copilot flow using env vars, gh CLI, or OAuth device code. The reasoning-effort step
    is the shared post-pick one in ``select_provider_and_model`` (Copilot's per-model level set
    comes from ``setup_reasoning_efforts`` there)."""
    from hermes_cli.auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials
    from hermes_cli.model_setup_flows_common import _persist_model, _pick_model_or_prompt, _say
    from hermes_cli.models import fetch_api_models

    from .wire import model_api_mode

    del config, args
    provider_id = "copilot"
    pconfig = PROVIDER_REGISTRY[provider_id]
    creds = resolve_api_key_provider_credentials(provider_id)
    api_key = creds.get("api_key", "")
    source = creds.get("source", "")
    if not api_key:
        if not _copilot_obtain_token():
            return
        creds = resolve_api_key_provider_credentials(provider_id)
        api_key = creds.get("api_key", "")
    else:
        if source in {"GITHUB_TOKEN", "GH_TOKEN"}:
            from hermes_cli.env_loader import format_secret_source_suffix
            _say(f"  GitHub token: {api_key[:8]}... ✓ ({source}{format_secret_source_suffix(source)})", "")
        else:
            _say("  GitHub token: ✓ (from `gh auth token`)" if source == "gh auth token" else "  GitHub token: ✓", "")

    effective_base = pconfig.inference_base_url
    catalog, live_models, _normalize = _copilot_catalog(api_key)
    if not catalog:
        live_models = fetch_api_models(api_key, effective_base)

    selected = _pick_model_or_prompt(
        _copilot_model_list(live_models), "Model name: ", current_model=_normalize(current_model),
        confirm_provider=provider_id, confirm_base_url=effective_base, confirm_api_key=api_key)
    if not selected:
        print("No change.")
        return
    selected = _normalize(selected)
    _persist_model(selected, provider_id, base_url=effective_base,
                   api_mode=model_api_mode(selected, catalog=catalog, api_key=api_key))
    print(f"Default model set to: {selected} (via {pconfig.name})")


def setup_reasoning_efforts(model: str) -> tuple:
    """Copilot publishes a per-model effort set; ``()`` when the model takes none."""
    from .catalog import github_model_reasoning_efforts
    return tuple(github_model_reasoning_efforts(model) or ())


# ── Desktop Accounts card + in-app device-code login ──────────────────────────


def account_status() -> dict[str, Any]:
    """Card status from the env-token ladder only (no ``gh`` subprocess, no token exchange: this runs
    on every Accounts list). A token here is managed on the Keys tab, so it reads as ``env_var``."""
    from hermes_cli.config import get_env_value
    from .credentials import COPILOT_ENV_VARS, validate_copilot_token
    from hermes_cli.env_loader import format_secret_source_suffix
    for var in COPILOT_ENV_VARS:
        token = (get_env_value(var) or "").strip()
        if token and validate_copilot_token(token)[0]:
            return {"logged_in": True, "source": "env_var", "source_label": f"{var}{format_secret_source_suffix(var)}",
                    "token_preview": None, "expires_at": None, "has_refresh_token": False}
    return {"logged_in": False, "source": None}


def start_device_login() -> dict[str, Any]:
    """Ask GitHub for a device code (the same OAuth app as ``copilot_device_code_login``)."""
    from .credentials import (
        COPILOT_OAUTH_CLIENT_ID, _DEVICE_CODE_POLL_INTERVAL, _DEVICE_CODE_POLL_SAFETY_MARGIN, _post_form)
    data = _post_form(f"{_DEVICE_LOGIN_BASE}/login/device/code",
                      {"client_id": COPILOT_OAUTH_CLIENT_ID, "scope": "read:user"}, 15)
    device_code, user_code = data.get("device_code", ""), data.get("user_code", "")
    if not device_code or not user_code:
        raise RuntimeError("GitHub did not return a device code.")
    interval = max(int(data.get("interval") or _DEVICE_CODE_POLL_INTERVAL), 1)
    return {"user_code": user_code, "verification_url": data.get("verification_uri") or _DEVICE_VERIFICATION_URL,
            "expires_in": int(data.get("expires_in") or _DEVICE_CODE_EXPIRES_IN),
            "interval": interval + _DEVICE_CODE_POLL_SAFETY_MARGIN,
            "state": {"device_code": device_code, "interval": interval}}


def poll_device_login(state: dict[str, Any]) -> dict[str, Any]:
    """One token poll. A transport blip is just another pending tick, as in the terminal flow."""
    from .credentials import COPILOT_OAUTH_CLIENT_ID, _DEVICE_CODE_POLL_SAFETY_MARGIN, _post_form
    fields = {"client_id": COPILOT_OAUTH_CLIENT_ID, "device_code": state["device_code"],
              "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}
    try:
        result = _post_form(f"{_DEVICE_LOGIN_BASE}/login/oauth/access_token", fields, 10)
    except (OSError, ValueError):   # URLError/timeouts are OSError; a garbled body is ValueError
        return {"status": "pending"}
    if result.get("access_token"):
        return {"status": "approved", "credential": result["access_token"]}
    error = result.get("error", "")
    if error == "slow_down":
        # RFC 8628: add 5 seconds to polling interval (or honor a server-supplied one)
        server_interval = result.get("interval")
        if isinstance(server_interval, (int, float)) and server_interval > 0:
            interval = int(server_interval)
        else:
            interval = int(state.get("interval") or 1) + 5
        return {"status": "pending", "interval": interval + _DEVICE_CODE_POLL_SAFETY_MARGIN,
                "state": {**state, "interval": interval}}
    if not error or error == "authorization_pending":
        return {"status": "pending"}
    status, message = _DEVICE_ERRORS.get(error, ("error", f"Authorization failed: {error}"))
    return {"status": status, "message": message}


def save_device_login(token: str) -> None:
    """Persist the GitHub OAuth token where the terminal device-code login puts it."""
    from hermes_cli.config import save_env_value
    save_env_value(_TOKEN_ENV_VAR, token)


SETUP_FIELDS: dict[str, Any] = {
    "model_setup_flow": model_flow_copilot,
    "setup_reasoning_efforts": setup_reasoning_efforts,
    "account_card": {"name": "GitHub Copilot", "docs_url": "https://docs.github.com/en/copilot"},
    "account_status": account_status,
    "start_device_login": start_device_login,
    "poll_device_login": poll_device_login,
    "save_device_login": save_device_login,
}
