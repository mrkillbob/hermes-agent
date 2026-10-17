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


def read_snapshot(ctx, board, *, expected_root=None, **selectors):
    # Keep this local check as well as the native parent check: RPC threads do
    # not carry a child's native fencing ContextVars. Host mode refuses this API.
    if PROTECTED_CONTEXT.intersection(os.environ):
        raise IntakeError("worker/delegation identity or Kanban path pin present")
    try:
        snapshot = ctx.kanban_readonly_snapshot(board, **selectors)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        raise IntakeError("passive Kanban context unavailable") from None
    if (not isinstance(snapshot, dict) or set(snapshot) != {"control_root", "board", "tasks"}
            or snapshot["board"] != board):
        raise IntakeError("exact passive Kanban context required")
    root = _canonical(snapshot["control_root"])
    if expected_root is not None and root != _canonical(expected_root):
        raise IntakeError("registered Kanban root differs")
    selected = any(value is not None for value in selectors.values())
    if (selected and not isinstance(snapshot["tasks"], list)) or (not selected and snapshot["tasks"] is not None):
        raise IntakeError("complete selected task snapshot required")
    return snapshot


def registered_control_root(ctx, board="default"):
    return _canonical(read_snapshot(ctx, board)["control_root"])


def require_context(ctx, expected_root, board):
    read_snapshot(ctx, board, expected_root=expected_root)
    return _canonical(expected_root)
