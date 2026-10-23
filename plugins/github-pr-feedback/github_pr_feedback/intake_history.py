"""Plugin-owned legacy receipt qualification over a complete passive snapshot."""

from hashlib import sha256
import json
import re
import time

from .intake_only import IntakeError

_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_LEGACY_ROWS = 10_000
_MAX_LEGACY_SCAN_BYTES = 8 * 1024 * 1024
_LEGACY_INDEX_SECONDS = 1.0
_monotonic = time.monotonic
_KINDS = frozenset({"issue_comment", "review_comment", "review", "pr_local_ci", "pr_repair", "pr_actions_needed"})
_IDENTITY_FIELDS = frozenset({"repository", "pr_number", "feedback_kind", "feedback_id"})
_HASHED_LEGACY = re.compile(
    r"github-pr-feedback:([0-9a-f]{64})(?::(typed-fixer-v3|supervised-v4|actions-needed-v1))?"
    r"(?::dispatch-([2-9]|[1-9][0-9]+))?")


def _strict_json(raw: str) -> dict:
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                raise ValueError("duplicate JSON key")
            result[name] = value
        return result

    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _text(value) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("canonical identity text required")
    return value


def _receipt_fields(evidence: dict, *, kind=None, feedback_id=None) -> tuple:
    repository = _text(evidence.get("repository"))
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise ValueError("invalid repository")
    number = evidence.get("pr_number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ValueError("invalid PR number")
    stored_kind = evidence.get("feedback_kind", kind)
    stored_id = evidence.get("feedback_id", feedback_id)
    if stored_kind not in _KINDS or (kind is not None and stored_kind != kind):
        raise ValueError("invalid feedback kind")
    identifier = _text(stored_id)
    if feedback_id is not None and identifier != feedback_id:
        raise ValueError("conflicting feedback identity")
    head = _text(evidence.get("expected_head_sha"))
    if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", head) is None:
        raise ValueError("invalid head")
    return repository, number, stored_kind, identifier, head


def _identity_tuple(value: dict) -> tuple:
    return (value["repository"].casefold(), value["pr_number"], value["feedback_kind"], value["feedback_id"])


def _parse_identity(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024:
        raise ValueError("identity input bound")
    value = _strict_json(raw)
    if value.keys() != _IDENTITY_FIELDS:
        raise ValueError("identity fields")
    _receipt_fields({**value, "expected_head_sha": "0" * 40})
    if json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) != raw:
        raise ValueError("canonical identity JSON required")
    return value


def _legacy_evidence(body: str) -> dict:
    if not isinstance(body, str):
        raise ValueError("missing legacy evidence")
    try:
        return _strict_json(body)
    except json.JSONDecodeError:
        return _strict_json(body.rsplit("\n", 1)[-1])


def _contract_receipt(evidence: dict, contract: str | None) -> tuple:
    if contract == "typed-fixer-v3":
        receipt_id = _text(evidence.get("ci_receipt_id"))
        if re.fullmatch(r"[0-9a-f]{64}", receipt_id) is None:
            raise ValueError("invalid typed receipt")
        return _receipt_fields(evidence, kind="pr_repair", feedback_id="ci-receipt:" + receipt_id)
    if contract == "actions-needed-v1":
        if evidence.get("reason") != "github_check_action_required":
            raise ValueError("unknown actions contract")
        return _receipt_fields(evidence, kind="pr_actions_needed", feedback_id="github-action-required")
    if contract == "supervised-v4":
        # The source also permits a no-base audit identity. Hash verification
        # below can prove that exact constant; omitted nonempty base SHA cannot
        # be recovered and therefore fails closed rather than being excluded.
        identifier = evidence.get("feedback_id", "local-ci-audit-v2")
        if not isinstance(identifier, str) or re.fullmatch(r"local-ci-audit-v2(?::[0-9a-f]{40})?", identifier) is None:
            raise ValueError("unknown supervised identity")
        return _receipt_fields(evidence, kind="pr_local_ci", feedback_id=identifier)
    return _receipt_fields(evidence)


def _qualified_legacy_identity(task: dict) -> tuple:
    key = task["idempotency_key"]
    evidence = _legacy_evidence(task["body"])
    if key.startswith("github-pr-feedback:intent-review:"):
        receipt = _receipt_fields(evidence)
        digest = key.rsplit(":", 1)[-1]
        expected = "github-pr-feedback:intent-review:" + ":".join(map(str, receipt)) + ":" + digest
        if (evidence.get("intent_review") is not True or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or expected != key):
            raise ValueError("unbound intent review")
    else:
        match = _HASHED_LEGACY.fullmatch(key)
        if match is None or (match[2] == "actions-needed-v1" and match[3] is not None):
            raise ValueError("unknown legacy key contract")
        receipt = _contract_receipt(evidence, match[2])
        if sha256(repr(receipt).encode("utf-8")).hexdigest() != match[1]:
            raise ValueError("unbound legacy identity")
    return receipt[0].casefold(), *receipt[1:4]


def _validate_task(task: dict, pattern: re.Pattern) -> None:
    stored = task["idempotency_key"]
    if not isinstance(stored, str) or pattern.fullmatch(stored) is None:
        raise ValueError("unexpected key")
    for name in ("id", "title", "status", "workspace_kind"):
        if not isinstance(task[name], str) or not task[name]:
            raise ValueError("invalid task identity")
    for name in ("body", "assignee", "workspace_path", "model_override",
                 "provider_override", "reasoning_effort", "branch_name", "skills"):
        if task[name] is not None and not isinstance(task[name], str):
            raise ValueError("invalid task field")


def project_legacy_receipt(task: dict) -> dict:
    """Qualify every original receipt before discarding unrelated body prose."""
    _validate_task(task, re.compile(r"github-pr-feedback:.*", re.DOTALL))
    identity = _qualified_legacy_identity(task)
    return {"id": task["id"], "status": task["status"],
            "idempotency_key": task["idempotency_key"], "identity": list(identity),
            "body_sha256": sha256(task["body"].encode("utf-8")).hexdigest()}


def legacy_matches(rows, identity, *, deadline=None, projected=False):
    """Qualify every prefix row before proving absence; never expose unrelated data."""
    try:
        identity = _parse_identity(json.dumps(identity, sort_keys=True, separators=(",", ":")))
        if not isinstance(rows, list) or len(rows) > _MAX_LEGACY_ROWS:
            raise ValueError("incomplete snapshot")
        deadline = _monotonic() + _LEGACY_INDEX_SECONDS if deadline is None else deadline
        pattern = re.compile(r"github-pr-feedback:.*", re.DOTALL)
        result, scanned, output = [], 0, 3
        for task in rows:
            if projected:
                if (not isinstance(task, dict) or set(task) != {
                        "id", "status", "idempotency_key", "identity", "body_sha256"}
                        or not isinstance(task["identity"], list) or len(task["identity"]) != 4
                        or not all(isinstance(task[name], str) and task[name]
                                   for name in ("id", "status", "idempotency_key", "body_sha256"))
                        or re.fullmatch(r"[0-9a-f]{64}", task["body_sha256"]) is None):
                    raise ValueError("unqualified receipt projection")
                qualified = tuple(task["identity"])
            else:
                _validate_task(task, pattern)
                qualified = _qualified_legacy_identity(task)
            encoded = json.dumps(task, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            length = len(encoded.encode("utf-8"))
            scanned += length
            if length > _MAX_OUTPUT_BYTES or scanned > _MAX_LEGACY_SCAN_BYTES or _monotonic() >= deadline:
                raise ValueError("legacy scan bound")
            if _monotonic() >= deadline:
                raise ValueError("legacy qualification deadline")
            if qualified != _identity_tuple(identity):
                continue
            output += length + bool(result)
            if output > _MAX_OUTPUT_BYTES:
                raise ValueError("matched output bound")
            result.append(task)
        if _monotonic() >= deadline:
            raise ValueError("legacy qualification deadline")
        return result
    except (KeyError, TypeError, ValueError, RecursionError):
        raise IntakeError("legacy data qualification required") from None
