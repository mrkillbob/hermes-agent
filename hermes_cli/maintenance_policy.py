"""Per-home controls for maintenance that can acquire code or replace user data."""
from __future__ import annotations

from pathlib import Path


def maintenance_policy(home: Path, name: str, choices: tuple[str, ...]) -> str:
    """Validate the home before a config fallback can grant automatic maintenance."""
    from hermes_cli.config import load_config_readonly, validate_user_config_file
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    from hermes_cli.managed_scope import get_managed_dir

    paths = [home / "config.yaml"]
    managed = get_managed_dir()
    if managed is not None:
        paths.append(managed / "config.yaml")
    for path in paths:
        try:
            path.stat()
        except FileNotFoundError:
            continue
        validate_user_config_file(path)
    token = set_hermes_home_override(home)
    try:
        updates = load_config_readonly().get("updates", {})
    finally:
        reset_hermes_home_override(token)
    if not isinstance(updates, dict):
        raise ValueError(f"{home}: updates must be a mapping")
    value = updates.get(name, "auto")
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{home}: updates.{name} must be one of {', '.join(choices)}")
    return value


def report_pending(pending: list[tuple[str, str]], followups: list | None) -> None:
    """Persist user-action obligations separately from retryable product work."""
    from hermes_cli.update_receipt import record_followup, record_user_action

    for step, reason in pending:
        print(f"  ⚠ Maintenance pending '{step}': {reason}", flush=True)
        record_user_action(step, reason)
        record_followup(step, reason, retry="resolve the maintenance policy or health issue before acceptance")
        if followups is not None:
            followups.append((step, reason))
