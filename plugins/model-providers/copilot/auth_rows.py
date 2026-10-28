"""The ``hermes auth`` registry row this profile mirrors into ``hermes_cli.auth.PROVIDER_REGISTRY``.

Credentials resolve like an api-key provider (env tokens, pool, ``hermes auth add copilot`` paste), while
``auth_type="copilot"`` names its own setup flow; the row keeps its historical place right after LM Studio.
"""

from typing import Any

AUTH_ROW_FIELDS: dict[str, Any] = {
    "registry_auth_type": "api_key",
    "base_url_env_var": "COPILOT_API_BASE_URL",
    "registry_after": "lmstudio",
}
