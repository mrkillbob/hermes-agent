"""GitHub Copilot CLI (``copilot --acp --stdio``) client: the generic ACP client plus Copilot's quirks."""

from __future__ import annotations

from typing import Any

from agent.acp_client import ACPClient

# Stderr fingerprint of the deprecated `gh copilot` extension. Require BOTH the product name
# AND a deprecation marker: the NEW `@github/copilot` CLI legitimately mentions "copilot-cli".
_DEPRECATION_REQUIRED = ("gh-copilot",)
_DEPRECATION_MARKERS = ("has been deprecated", "no commands will be executed")
_DEPRECATED_CLI_ERROR = (
    "Hermes ACP mode requires the NEW GitHub Copilot CLI (github.com/github/copilot-cli), but the binary it just "
    "spawned is the deprecated `gh copilot` extension.\n\n"
    "Install the new CLI:\n  npm install -g @github/copilot\n  # then verify with: copilot --help\n\n"
    "If `copilot` already resolves to the new CLI but you still see this,\npoint Hermes at it explicitly:\n"
    "  export HERMES_COPILOT_ACP_COMMAND=/path/to/new/copilot\n\n"
    "Alternative: use the `copilot` provider (no ACP, hits the Copilot API\ndirectly with a Copilot subscription "
    "token) via `hermes setup`.\n\nOriginal error:\n"
)


def is_gh_copilot_deprecation_message(stderr_text: str) -> bool:
    """True iff stderr looks like the deprecated gh-copilot extension's banner."""
    lower = stderr_text.lower()
    return any(req in lower for req in _DEPRECATION_REQUIRED) and any(m in lower for m in _DEPRECATION_MARKERS)


class CopilotACPClient(ACPClient):
    """ACP client for the GitHub Copilot CLI."""

    # Same launch spec as the copilot-acp profile's process_* fields (the fallback when a
    # caller passes no command/args).
    default_command = "copilot"
    default_args = ("--acp", "--stdio")
    command_env_vars = ("HERMES_COPILOT_ACP_COMMAND", "COPILOT_CLI_PATH")
    args_env_var = "HERMES_COPILOT_ACP_ARGS"
    marker_base_url = "acp://copilot"
    placeholder_model = "copilot-acp"
    label = "Copilot ACP"
    missing_command_hint = "Install GitHub Copilot CLI or set HERMES_COPILOT_ACP_COMMAND/COPILOT_CLI_PATH."
    unsupported_transport_hint = (
        "Either install a CLI that ships with --acp support (e.g. `@github/copilot` late 2025+), or set "
        "HERMES_COPILOT_ACP_COMMAND / HERMES_COPILOT_ACP_ARGS to a working pair.")

    def model_entry_disabled(self, entry: dict[str, Any]) -> bool:
        """Copilot marks org-policy-disabled models with ``_meta.copilotEnablement: disabled``."""
        return str((entry.get("_meta") or {}).get("copilotEnablement") or "").strip().lower() == "disabled"

    def early_exit_error(self, stderr_text: str) -> str | None:
        return _DEPRECATED_CLI_ERROR + stderr_text if is_gh_copilot_deprecation_message(stderr_text) else None
