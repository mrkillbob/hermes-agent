"""Admit only unfenced intake in the registered control plane's Kanban root.

Protected identities and pins are refused, never removed or overridden. Native
host resolution remains authoritative, including its in-process fences.
"""

from __future__ import annotations

import os
from pathlib import Path

from .intake_only import IntakeError

PROTECTED_CONTEXT = frozenset(
    {
        "HERMES_KANBAN_TASK",
        "HERMES_KANBAN_RUN_ID",
        "HERMES_KANBAN_CLAIM_LOCK",
        "HERMES_KANBAN_GOAL_MODE",
        "HERMES_KANBAN_GOAL_MAX_TURNS",
        "HERMES_DELEGATED_CHILD_CONTEXT",
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_WORKSPACES_ROOT",
        "HERMES_KANBAN_ATTACHMENTS_ROOT",
    }
)


def _canonical(path):
    value = Path(path).expanduser()
    if not value.is_absolute():
        raise IntakeError("absolute registered intake root required")
    return value.resolve()


def registered_control_root(ctx):
    from hermes_constants import get_default_hermes_root

    manager = getattr(ctx, "_manager", None)
    homes = [getattr(manager, key, None) for key in ("home_path", "scope_key")]
    homes = [value for value in homes if value is not None]
    if not homes:
        raise IntakeError("registered intake control plane unavailable")
    roots = {_canonical(get_default_hermes_root(home=_canonical(value))) for value in homes}
    if len(roots) != 1:
        raise IntakeError("registered intake control roots differ")
    root = roots.pop()
    require_context(root)
    return root


def require_context(expected_root):
    # Presence is intentional: empty/'false' markers do not grant authority.
    if PROTECTED_CONTEXT.intersection(os.environ):
        raise IntakeError("worker/delegation identity or Kanban path pin present")
    from hermes_cli import kanban_db as kb
    from hermes_constants import get_default_hermes_root

    root = _canonical(expected_root)
    if _canonical(get_default_hermes_root()) != root or _canonical(kb.kanban_home()) != root:
        raise IntakeError("intake Kanban/control root mismatch")
    if (
        "HERMES_CONTROL_HOME" in os.environ
        and _canonical(os.environ["HERMES_CONTROL_HOME"]) != root
    ):
        raise IntakeError("inherited intake control root differs")
    if kb._explicit_board_intent_pinned():
        raise IntakeError("native Kanban context is fenced")
    return root
