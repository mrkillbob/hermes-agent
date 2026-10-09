"""Typed exact-head identity for existing Kanban PR automation policy."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

_PR_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PR_IDENTITY_FIELDS = frozenset({"repository", "pr_number", "expected_head_sha"})
_PR_HEAD_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_PR_READ_ONLY_ACTIONS = frozenset({
    "verify", "review", "inspect", "audit", "check", "read",
    "verify_ci_receipt", "review_exact_head_evidence", "check_mergeability",
    "review_mergeability", "inspect_merge_conflicts", "read_credit_metadata",
})

_PR_WRITE_ACTION_RE = re.compile(
    r"\b(?:edit|approve|merge|publish|repair|fix|push|reply|respond|base[-_ ]?refresh|"
    r"refresh(?:ing)?\s+(?:the\s+)?base|resolve(?:d|s|ing)?\s+(?:a\s+)?merge\s+conflict)\b",
    re.IGNORECASE,
)
_PR_PROHIBITED_WRITE_RE = re.compile(
    r"\b(?:do\s+not|don['’]t|never|must\s+not|should\s+not)\s+"
    r"(?:edit|repair|fix|push|publish|reply|respond|approve|merge)\b"
    r"(?:\s*(?:,\s*(?:(?:and|or)\s+)?|(?:and|or)\s+)"
    r"(?:edit|repair|fix|push|publish|reply|respond|approve|merge)\b)*",
    re.IGNORECASE,
)
_PR_READ_TARGET_RE = re.compile(
    r"\b(?:review|verify|inspect|audit|check|read)\s+"
    r"(?:(?:the|a|an|proposed|previous|existing|failed|planned|attempted|recorded|blocked)\s+)*"
    r"(?:edit|approval|approve|merge|publish|fix|repair|push|reply|response|base[-_ ]?refresh)\b",
    re.IGNORECASE,
)


def _pr_task_has_text_write_intent(payload: Mapping[str, Any], body: Optional[str], title: str) -> bool:
    # Validated identity fields and the typed action are data, not prose requests.
    prose_payload = {key: value for key, value in payload.items()
                     if key not in {"repository", "pr_number", "expected_head_sha", "action"}}
    try:
        json.loads(body or "")
        prefix = ""
    except (TypeError, ValueError):
        prefix = (body or "").rstrip().rsplit("\n", 1)[0]
    # Scan decoded prose so JSON escapes cannot hide normal word boundaries.
    parts = [title, prefix]
    pending = [prose_payload]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Mapping):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    text = "\n".join(parts)
    # Keep cooperative prohibitions and read targets distinct from write requests.
    # A later affirmative request in the same card still demands a write owner.
    text = _PR_PROHIBITED_WRITE_RE.sub("", text)
    text = _PR_READ_TARGET_RE.sub("", text)
    return _PR_WRITE_ACTION_RE.search(text) is not None


def _pr_task_payload(body: Optional[str]) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(body or "")
    except (TypeError, ValueError):
        # The existing feedback CLI appends its JSON evidence on the last line.
        try:
            payload = json.loads((body or "").rstrip().rsplit("\n", 1)[-1])
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


def classify_pr_task(
    body: Optional[str], *, title: str = "", idempotency_key: Optional[str] = None,
) -> Optional[str]:
    payload = _pr_task_payload(body)
    marked = (idempotency_key or "").strip().casefold().startswith("github-pr-feedback:")
    if payload is None or (not marked and not _PR_IDENTITY_FIELDS.issubset(payload)):
        return None
    _validate_exact_pr_identity(payload)
    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        # Legacy rendered cards bind exact identity and derive intent from prose.
        try:
            json.loads(body or "")
        except (TypeError, ValueError):
            return "write" if _pr_task_has_text_write_intent(payload, body, title) else "read"
        return "write"
    return "read" if (
        action.strip().casefold() in _PR_READ_ONLY_ACTIONS
        and not _pr_task_has_text_write_intent(payload, body, title)
    ) else "write"


def _canonical_pr_task_identity(body: Optional[str]) -> tuple[object, ...] | None:
    payload = _pr_task_payload(body)
    if payload is None or not _PR_IDENTITY_FIELDS.issubset(payload):
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

