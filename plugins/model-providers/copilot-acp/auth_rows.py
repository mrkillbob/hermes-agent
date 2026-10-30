"""The ``hermes auth`` registry row this profile mirrors into ``hermes_cli.auth.PROVIDER_REGISTRY``:
right after the ``copilot`` row, with its ``acp://`` base URL overridable from the environment."""

from typing import Any

AUTH_ROW_FIELDS: dict[str, Any] = {
    "base_url_env_var": "COPILOT_ACP_BASE_URL",
    "registry_after": "copilot",
}
