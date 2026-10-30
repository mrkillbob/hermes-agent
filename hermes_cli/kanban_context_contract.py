"""Pure bound task data and compact briefs; no runtime/authority integration.

All text and origin labels remain untrusted data. Rendering requires a caller-
bound admission policy; it is not an authorization or acceptance receipt. Final
outbound request checks, artifact access checks and route qualification remain
the existing controller's responsibility. No profiles, files or providers read.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from hermes_cli.kanban_event_contract import (
    ContractError, _DIGEST, _GRAPH_FIELDS, _SHA, _dependencies, _execution_identity,
    _integer, _object, _plain_json, _reference, _repository, _require, _scope,
)

SCHEMA_NAME = "hermes.task.context"
SCHEMA_VERSION = "1.0"
MAX_CONTEXT_BYTES = 64 * 1024
_IDENTITY_FIELDS = frozenset({
    "scope_id", "board", "task_id", "run_id", "root_task_id", "dependency_parent_ids",
    "parent_run_ref", "worker", "profile", "repository_ref",
})
_CONTENT_FIELDS = frozenset({
    "schema", "version", "identity", "contract_revision", "brief", "deliverable",
    "acceptance_criteria", "constraints", "selection", "budgets", "workspace_ref",
    "artifacts", "approved_history",
})
_SELECTION_FIELDS = frozenset({
    "model_source", "effort_source", "requested_provider", "requested_model", "requested_effort",
    "resolved_provider", "resolved_model", "resolved_effort", "profile_revision",
})
_BUDGET_FIELDS = frozenset({
    "context_tokens", "handoff_tokens", "max_runtime_seconds", "max_retries", "max_goal_turns",
    "hard_token_limit", "hard_cost_limit_microunits",
})
_ROUTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
_MEDIA_TYPE = re.compile(r"[A-Za-z0-9.+-]{1,64}/[A-Za-z0-9.+-]{1,64}\Z")
_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})


def _encoded(value: Any) -> str:
    return json.dumps(_plain_json(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _text(value: Any, field: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), field)
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ContractError(f"Invalid coordination contract: {field}") from None
    _require(size <= 16 * 1024 and all(ord(char) >= 32 or char in "\n\t" for char in value), field)


def _identity(value: Any) -> None:
    row = _object(value, _IDENTITY_FIELDS, "identity", _GRAPH_FIELDS)
    _scope(row["scope_id"], "identity.scope_id")
    for field in ("board", "task_id"):
        _reference(row[field], f"identity.{field}")
    _integer(row["run_id"], "identity.run_id", minimum=1)
    _reference(row["root_task_id"], "identity.root_task_id", nullable=True)
    _dependencies(row)
    _execution_identity(row)
    _repository(row["repository_ref"])


def _selection(value: Any, profile: Any) -> None:
    row = _object(value, _SELECTION_FIELDS, "selection")
    for field in _SELECTION_FIELDS - {"model_source", "effort_source", "profile_revision"}:
        item = row[field]
        _require(item is None or isinstance(item, str) and _ROUTE.fullmatch(item) is not None, "selection.route")
    _reference(row["profile_revision"], "selection.profile_revision", nullable=True)
    for source, fields in (("model_source", ("provider", "model")), ("effort_source", ("effort",))):
        _require(row[source] in ("task", "profile"), "selection.source")
        if row[source] == "profile":
            _require(profile is not None and row["profile_revision"] is not None, "selection.profile_revision")
        for field in fields:
            requested, resolved = row[f"requested_{field}"], row[f"resolved_{field}"]
            if row[source] == "task":
                _require(requested is not None and (resolved is None or resolved == requested), "selection.explicit")
            else:
                _require(requested is None, "selection.inherited")


def _budgets(value: Any) -> None:
    row = _object(value, _BUDGET_FIELDS, "budgets")
    for field in _BUDGET_FIELDS:
        minimum = 0 if field in ("max_retries", "hard_cost_limit_microunits") else 1
        _integer(row[field], "budgets.limit", minimum=minimum, nullable=field not in ("context_tokens", "handoff_tokens"))


def _rows(value: Any, fields: frozenset[str], name: str, *, cap: int, minimum: int = 0) -> list:
    _require(type(value) is list and minimum <= len(value) <= cap, name)
    ids = []
    for row in value:
        _object(row, fields, name)
        _reference(row["id"], f"{name}.id")
        ids.append(row["id"])
    _require(len(set(ids)) == len(ids), f"{name}.ids")
    return value


def _required_text(record: Mapping) -> None:
    for field in ("brief", "deliverable"):
        _text(record[field], field)
    for row in _rows(record["acceptance_criteria"], frozenset({"id", "text", "evidence_refs"}), "acceptance_criteria", cap=64, minimum=1):
        _text(row["text"], "acceptance_criteria.text")
        refs = row["evidence_refs"]
        _require(type(refs) is list and len(refs) <= 32, "acceptance_criteria.evidence_refs")
        for ref in refs:
            _reference(ref, "acceptance_criteria.evidence_ref")
    for row in _rows(record["constraints"], frozenset({"id", "text", "origin_ref"}), "constraints", cap=64):
        _text(row["text"], "constraints.text")
        _reference(row["origin_ref"], "constraints.origin_ref")


def _artifacts(value: Any) -> None:
    fields = frozenset({"id", "digest", "media_type", "origin_task_id", "origin_run_id", "head"})
    for row in _rows(value, fields, "artifacts", cap=32):
        digest, media, head = row["digest"], row["media_type"], row["head"]
        _require(isinstance(digest, str) and _DIGEST.fullmatch(digest) is not None, "artifacts.digest")
        _require(isinstance(media, str) and _MEDIA_TYPE.fullmatch(media) is not None, "artifacts.media_type")
        _require(head is None or isinstance(head, str) and _SHA.fullmatch(head) is not None, "artifacts.head")
        _reference(row["origin_task_id"], "artifacts.origin_task_id")
        _integer(row["origin_run_id"], "artifacts.origin_run_id", minimum=1)


def _content(record: Mapping) -> dict:
    record = _object(record, _CONTENT_FIELDS, "context")
    _require(record["schema"] == SCHEMA_NAME and record["version"] == SCHEMA_VERSION, "context.schema/version")
    _identity(record["identity"])
    _reference(record["contract_revision"], "contract_revision")
    _reference(record["workspace_ref"], "workspace_ref", nullable=True)
    _required_text(record)
    _selection(record["selection"], record["identity"]["profile"])
    _budgets(record["budgets"])
    _artifacts(record["artifacts"])
    for row in _rows(record["approved_history"], frozenset({"id", "text", "approval_ref", "producer_ref"}), "approved_history", cap=32):
        _text(row["text"], "approved_history.text")
        for field in ("approval_ref", "producer_ref"):
            _reference(row[field], f"approved_history.{field}")
    encoded = _encoded(record)
    _require(len(encoded.encode("utf-8")) <= MAX_CONTEXT_BYTES, "context.size")
    return json.loads(encoded)


def _digest(record: Mapping) -> str:
    return hashlib.sha256(_encoded(record).encode("utf-8")).hexdigest()


def _complete_record(content: Mapping, digest: str) -> dict:
    record = {**content, "contract_digest": digest}
    _require(len(_encoded(record).encode("utf-8")) <= MAX_CONTEXT_BYTES, "context.size")
    return record


def build_context(
    *, identity: Mapping, contract_revision: str, brief: str, deliverable: str,
    acceptance_criteria: list, constraints: list, selection: Mapping, budgets: Mapping,
    workspace_ref: str | None, artifacts: list | None = None, approved_history: list | None = None,
) -> dict:
    """Create immutable-by-digest task data; never resolves live profile state."""
    _require(isinstance(budgets, Mapping), "budgets")
    record = _content({
        "schema": SCHEMA_NAME, "version": SCHEMA_VERSION, "identity": identity,
        "contract_revision": contract_revision, "brief": brief, "deliverable": deliverable,
        "acceptance_criteria": acceptance_criteria, "constraints": constraints, "selection": selection,
        "budgets": {"context_tokens": 2000, "handoff_tokens": 400, **budgets},
        "workspace_ref": workspace_ref, "artifacts": [] if artifacts is None else artifacts,
        "approved_history": [] if approved_history is None else approved_history,
    })
    return _complete_record(record, _digest(record))


def validate_context(record: Mapping) -> dict:
    """Validate detached data/digest, not approval, provenance or permissions."""
    record = _object(record, _CONTENT_FIELDS | {"contract_digest"}, "context")
    digest = record["contract_digest"]
    _require(isinstance(digest, str) and _DIGEST.fullmatch(digest) is not None, "contract_digest")
    content = _content({key: value for key, value in record.items() if key != "contract_digest"})
    _require(_digest(content) == digest, "contract_digest")
    return _complete_record(content, digest)


def validate_bound_context(record: Mapping, *, expected_identity: Mapping, expected_digest: str) -> dict:
    """Check a previously trusted binding supplied by the controller, without I/O."""
    validated = validate_context(record)
    _identity(expected_identity)
    _require(validated["identity"] == _plain_json(expected_identity), "context.binding_identity")
    _require(validated["contract_digest"] == expected_digest, "context.binding_digest")
    return validated


def _hold_reason(record: Mapping) -> str | None:
    selection = record["selection"]
    if any(selection[field] is None for field in ("resolved_provider", "resolved_model", "resolved_effort")):
        return "selection_unresolved"
    if selection["resolved_effort"] not in _EFFORTS:
        return "effort_unsupported"
    if any(record["budgets"][field] is not None for field in ("hard_token_limit", "hard_cost_limit_microunits")):
        return "hard_budget_unqualified"
    return None


def render_compact_context(
    record: Mapping, *, count_tokens: Callable[[str], int], count_method: str,
    admit_text: Callable[[str], str],
) -> dict:
    """Pack approved selections; ready means rendered data, never dispatch permission.

    Caller binds trusted count/admission functions. Estimated caps are soft;
    reported counts describe this brief, not runtime token/cost consumption.
    """
    record = validate_context(record)
    _require(count_method in ("measured", "estimate_rough"), "context.count_method")
    result = {"status": "held", "reason_code": _hold_reason(record), "text": None,
              "contract_digest": record["contract_digest"], "input_tokens": None,
              "count_method": count_method, "retained_history_ids": [], "dropped_history_count": 0}
    if result["reason_code"] is not None:
        return result
    brief = {**record, "schema": "hermes.task.brief"}
    history = brief["approved_history"]
    while True:
        text = _encoded(brief)
        count = count_tokens(text)
        _integer(count, "context.token_count", minimum=1)
        if count <= record["budgets"]["context_tokens"]:
            break
        if not history:
            return {**result, "reason_code": "required_context_overflow", "input_tokens": count}
        history.pop()
        result["dropped_history_count"] += 1
    try:
        admitted = admit_text(text)
    except Exception:  # health: allow BLE001 -- admission callback errors may contain sensitive text; return a content-free hold
        return {**result, "reason_code": "text_not_admitted"}
    if admitted != text:
        return {**result, "reason_code": "text_not_admitted"}
    return {**result, "status": "ready", "reason_code": None, "text": text, "input_tokens": count,
            "retained_history_ids": [row["id"] for row in history]}
