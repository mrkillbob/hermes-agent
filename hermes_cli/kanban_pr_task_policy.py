"""Typed exact-head identity for existing Kanban PR automation policy."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

_PR_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PR_HEAD_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_PR_READ_ONLY_ACTIONS = frozenset({
    "verify", "review", "inspect", "audit", "check", "read",
    "verify_ci_receipt", "review_exact_head_evidence", "check_mergeability",
    "review_mergeability", "inspect_merge_conflicts", "read_credit_metadata",
})

def _pr_task_payload(body: Optional[str]) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(body or "")
    except (TypeError, ValueError):
        # The existing feedback CLI appends its JSON evidence on the last line.
        try:
            payload = json.loads((body or "").rsplit("\n", 1)[-1])
        except (TypeError, ValueError):
            return None
    return payload if isinstance(payload, dict) else None


def _validate_exact_pr_identity(payload: Mapping[str, Any]) -> None:
    repository = payload.get("repository")
    pr_number = payload.get("pr_number")
    expected_head_sha = payload.get("expected_head_sha")
    if (
        not isinstance(repository, str)
        or _PR_REPOSITORY_RE.fullmatch(repository) is None
        or isinstance(pr_number, bool)
        or not isinstance(pr_number, int)
        or pr_number <= 0
        or not isinstance(expected_head_sha, str)
        or _PR_HEAD_RE.fullmatch(expected_head_sha) is None
    ):
        raise ValueError(
            "typed pull-request task requires an exact PR identity: "
            "repository owner/name, positive pr_number, and full expected_head_sha"
        )


def classify_pr_task(body: Optional[str]) -> Optional[str]:
    payload = _pr_task_payload(body)
    if payload is None or "pr_number" not in payload:
        return None
    _validate_exact_pr_identity(payload)
    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        # Legacy rendered cards have no typed action: retain their existing
        # title/instruction authority policy while binding exact identity.
        try:
            json.loads(body or "")
        except (TypeError, ValueError):
            return None
        return "write"
    return "read" if action.strip().casefold() in _PR_READ_ONLY_ACTIONS else "write"


def _canonical_pr_task_identity(body: Optional[str]) -> tuple[object, ...] | None:
    payload = _pr_task_payload(body)
    if payload is None or "pr_number" not in payload:
        return None
    _validate_exact_pr_identity(payload)
    action = payload.get("action")
    return (
        payload["repository"], payload["pr_number"], payload["expected_head_sha"],
        action.strip().casefold() if isinstance(action, str) else None,
    )


def validate_pr_task_identity_transition(
    *, existing_body: Optional[str], replacement_body: Optional[str]
) -> None:
    existing_identity = _canonical_pr_task_identity(existing_body)
    if existing_identity is None:
        return
    if _canonical_pr_task_identity(replacement_body) != existing_identity:
        raise ValueError("specification must preserve exact pull-request identity and action")

