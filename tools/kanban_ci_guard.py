"""Core Kanban completion gate for governed local-CI tasks.

This module intentionally has no plugin imports: dispatcher workers use their
assignee profile, so the feedback plugin may be disabled even though the
worker still owns a governed local-CI task.
"""
from __future__ import annotations

import os
from collections.abc import Callable


_BLOCK_MESSAGE = (
    "CI completion rejected: no typed passing durable CI receipt matches this task's exact PR head/base "
    "and dispatch. Run the governed github-pr-feedback audit-pr command; its deterministic receipt and "
    "handoff own completion. If audit cannot run, use kanban_block with the exact blocker. A summary or "
    "claimed command is not CI evidence."
)
_UNAVAILABLE_MESSAGE = (
    "CI completion rejected: the durable CI task binding or receipt could not be verified. "
    "Run the governed github-pr-feedback audit-pr command; its deterministic receipt and handoff own "
    "completion. If audit cannot run, use kanban_block with the exact blocker."
)


_POLICIES: list[Callable[[str | None], str | None]] = []


def register_completion_policy(policy: Callable[[str | None], str | None]) -> None:
    """Register an optional, capability-owned completion policy."""
    if policy not in _POLICIES:
        _POLICIES.append(policy)


def completion_block(task_id: str | None = None) -> str | None:
    """Run registered completion policies without importing any plugin."""
    if not os.environ.get("HERMES_KANBAN_TASK", "").strip():
        return None
    for policy in _POLICIES:
        rejection = policy(task_id)
        if rejection is not None:
            return rejection
    return _UNAVAILABLE_MESSAGE if os.environ.get("HERMES_KANBAN_COMPLETION_GATE") else None
