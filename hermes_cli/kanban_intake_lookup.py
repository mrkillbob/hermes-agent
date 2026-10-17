"""Passive intake and legacy feedback lookup; no board initialization."""

from __future__ import annotations

from contextlib import closing
from dataclasses import fields
from hashlib import sha256
import json
import re
import sqlite3
import sys
import time


_INTAKE_KEY = re.compile(r"github-pr-intake:v1:[0-9a-f]{64}")
_LEGACY_KEY = re.compile(r"github-pr-feedback:[0-9a-f]{64}")
_LEGACY_SUFFIXES = r"(?::(?:typed-fixer-v3|supervised-v4))?(?::dispatch-(?:[2-9]|[1-9][0-9]+))?"
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


def _lookup_payload(board: str, key: str | None, *, legacy: bool = False, identity: dict | None = None) -> str:
    from hermes_cli import kanban_db as kb

    slug = kb._normalize_board_slug(board)
    if not slug or not kb.board_exists(slug):
        raise ValueError("explicit existing board required")
    # The existing resolver retains worker/delegation pins. Resolve once and
    # open only that existing canonical file, never the current-board fallback.
    path = kb.kanban_db_path(board=slug).resolve(strict=True)
    if not path.is_file():
        raise ValueError("existing board database required")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_OUTPUT_BYTES)
        connection.row_factory = sqlite3.Row
        deadline = None
        if identity is not None:
            deadline = _monotonic() + _LEGACY_INDEX_SECONDS
            connection.set_progress_handler(lambda: int(_monotonic() >= deadline), 1000)
            connection.execute("BEGIN")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        if not {field.name for field in fields(kb.Task)}.issubset(columns):
            raise ValueError("incomplete task schema")
        # No status predicate: archived cards still fence duplicate creation.
        rows = _matching_rows(connection, key, legacy=legacy, identity=identity is not None)
        pattern = (re.compile(r"github-pr-feedback:.*", re.DOTALL) if identity is not None else
                   re.compile(re.escape(key) + (_LEGACY_SUFFIXES if legacy else "")))
        encoded = []
        size = 3  # array brackets and final newline
        scanned_rows = scanned_bytes = 0
        for row in rows:
            task = dict(row)
            _validate_task(task, pattern)
            item = json.dumps(task, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if identity is not None:
                scanned_rows += 1
                scanned_bytes += len(item.encode("utf-8"))
                if (scanned_rows > _MAX_LEGACY_ROWS or scanned_bytes > _MAX_LEGACY_SCAN_BYTES
                        or _monotonic() >= deadline):
                    raise ValueError("legacy index bound")
                qualified = _qualified_legacy_identity(task)
                if _monotonic() >= deadline:
                    raise ValueError("legacy index deadline")
                if qualified != _identity_tuple(identity):
                    continue
            size += len(item.encode("utf-8")) + bool(encoded)
            if size > _MAX_OUTPUT_BYTES:
                raise ValueError("lookup output exceeds limit")
            encoded.append(item)
        if deadline is not None and _monotonic() >= deadline:
            raise ValueError("legacy index deadline")
    return "[" + ",".join(encoded) + "]"


def _matching_rows(connection, key: str | None, *, legacy: bool, identity: bool = False):
    if identity:
        return connection.execute(
            "SELECT * FROM tasks WHERE idempotency_key GLOB 'github-pr-feedback:*' ORDER BY id")
    if not legacy:
        return connection.execute("SELECT * FROM tasks WHERE idempotency_key = ? ORDER BY id", (key,))
    # These are the source-backed legacy constructors, including a generation
    # appended after the typed/supervised suffix. Never scan other receipts.
    bases = (key, key + ":typed-fixer-v3", key + ":supervised-v4")
    clauses = ["idempotency_key IN (?, ?, ?)"]
    parameters = list(bases)
    for base in bases:
        prefix = base + ":dispatch-"
        clauses.append("(idempotency_key GLOB ? AND substr(idempotency_key, ?) NOT GLOB '*[^0-9]*' "
                       "AND substr(idempotency_key, ?) != '1')")
        parameters.extend((prefix + "[1-9]*", len(prefix) + 1, len(prefix) + 1))
    return connection.execute("SELECT * FROM tasks WHERE " + " OR ".join(clauses) + " ORDER BY id", parameters)


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


def intake_lookup_command(args) -> int:
    intake_key = getattr(args, "idempotency_key", None)
    legacy_key = getattr(args, "legacy_receipt_key", None)
    raw_identity = getattr(args, "legacy_feedback_identity", None)
    identity = None
    legacy = legacy_key is not None
    key = legacy_key if legacy else intake_key
    pattern = _LEGACY_KEY if legacy else _INTAKE_KEY
    if sum(value is not None for value in (intake_key, legacy_key, raw_identity)) != 1:
        print("kanban intake-lookup: exactly one canonical intake or legacy SHA256 key required", file=sys.stderr)
        return 2
    try:
        if raw_identity is not None:
            identity = _parse_identity(raw_identity)
        elif not isinstance(key, str) or pattern.fullmatch(key) is None:
            raise ValueError("invalid key")
    except (TypeError, ValueError):
        print("kanban intake-lookup: canonical selector required", file=sys.stderr)
        return 2
    if not getattr(args, "board", None) or not getattr(args, "json", False):
        print("kanban intake-lookup: explicit --board and --json required", file=sys.stderr)
        return 2
    try:
        payload = _lookup_payload(args.board, key, legacy=legacy, identity=identity)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        # Rows can contain task bodies. Errors never echo DB values or a partial
        # payload that a caller could mistake for a successful reconciliation.
        message = ("legacy data qualification required" if identity is not None else
                   "existing board could not be read safely")
        print("kanban intake-lookup: " + message, file=sys.stderr)
        return 1
    print(payload)
    return 0
