"""Heal mixed ``sys.modules`` after an in-place checkout update.

Pre-reexec updaters (Hermes ≤ v2026.9.14) purged only package prefixes
(``hermes_cli``, ``gateway``, ``tools``, ``tui_gateway``, ``agent``) and left
root modules like ``utils`` cached in the updater process. The post-pull
gateway-restart phase then imports new ``hermes_cli.gateway`` /
``hermes_cli.config`` into that process; those need symbols the stale
``utils`` lacks (``file_signature``), and ``hermes update`` exits 1 with
``gateway auto-restart failed: cannot import name 'file_signature' from
'utils'``.

Post-swap hand-off (``hermes_cli.update_handoff``) makes this class dead for
updaters that already include it. This module bridges upgrades from
pre-handoff releases by dropping incomplete cached modules before updated
code imports symbols added by the pulled tree.
"""

from __future__ import annotations

import sys
from typing import Mapping, Sequence

# Modules a pre-handoff updater can leave behind, keyed by symbols introduced
# after the cached copy. Extend this when a post-pull import fails against an
# incomplete module that survived the legacy package purge.
_MODULE_REQUIRED_ATTRS: dict[str, tuple[str, ...]] = {
    "utils": ("file_signature",),
    "hermes_cli.config": ("drop_stale_root_modules",),
    "hermes_cli.tools_config": ("_configurable_keys", "_parse_enabled_flag"),
    "hermes_cli.config_migrations": ("_migrate_to_46",),
    "gateway.status": ("profile_flag_value",),
}

_MODULE_REQUIRED_MIGRATIONS: dict[str, tuple[int, ...]] = {
    # The step function may exist in a stale module whose migration ladder did
    # not register it before an in-place checkout update.
    "hermes_cli.config_migrations": (46,),
}


def drop_stale_root_modules(
    required: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """Drop cached root modules missing required attrs. Returns dropped names."""
    checks = _MODULE_REQUIRED_ATTRS if required is None else required
    dropped: list[str] = []
    for name, attrs in checks.items():
        mod = sys.modules.get(name)
        if mod is None:
            continue
        # Python publishes a module in sys.modules before executing its body.
        # A nested import can reach this bridge while that module is still being
        # initialized; an absent newly-added attribute is not evidence of staleness.
        if getattr(getattr(mod, "__spec__", None), "_initializing", False):
            continue
        missing_attr = any(not hasattr(mod, attr) for attr in attrs)
        required_migrations = _MODULE_REQUIRED_MIGRATIONS.get(name, ())
        migrations = getattr(mod, "MIGRATIONS", ())
        missing_migration = any(
            not any(isinstance(entry, tuple) and entry and entry[0] == version for entry in migrations)
            for version in required_migrations
        )
        if missing_attr or missing_migration:
            sys.modules.pop(name, None)
            dropped.append(name)
    return dropped
