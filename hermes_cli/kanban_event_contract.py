"""Pure producer boundary for ``hermes.coordination.event`` 1.0.

Initial types: task.claimed, run.ended, context.bound, context.compacted.
No storage, transport, identity lookup, controller action or acceptance authority.
Callers supply trusted noncredential references and committed store sequences;
shape validation is neither secret detection nor proof of provenance/ownership.
Unknown versions/types/fields are refused at this producer boundary. Future
observation readers may ignore extensions; action consumers must negotiate them.
No legacy payload is copied automatically. No DeerFlow code is incorporated.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

SCHEMA_NAME = "hermes.coordination.event"
SCHEMA_VERSION = "1.0"
MAX_EVENT_BYTES = 16 * 1024
MAX_DEPENDENCY_REFS = 32
_MAX_INTEGER = 2**63 - 1
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,511}\Z")
_SHA = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_IDENTITY_FIELDS = frozenset({
    "scope_id", "board", "task_id", "run_id", "root_task_id", "dependency_parent_ids",
    "parent_run_ref", "worker", "profile", "repository_ref", "source_ref", "causation_id",
})
_GRAPH_FIELDS = frozenset({"dependency_graph_ref", "dependency_count"})
_ENVELOPE_FIELDS = frozenset({"schema", "version", "type", "seq", "event_id", "created_at", "observed_at", "payload"})
_END_REASONS = {
    "completed": {"completed"}, "cancelled": {"cancelled"}, "timed_out": {"timeout"},
    "crashed": {"worker_lost", "failed", "unknown"},
    "failed": {"failed", "provider_unavailable", "policy_hold", "unknown"},
    "paused": {"cancelled", "policy_hold", "unknown"},
    "reclaimed": {"claim_reclaimed", "worker_lost", "timeout", "unknown"},
    "blocked": {"policy_hold", "provider_unavailable", "unknown"}, "unknown": {"unknown"},
    # Preserve existing durable run labels; none grants acceptance or quiescence.
    "review_requested": {"review_requested", "unknown"},
    "changes_requested": {"changes_requested", "unknown"},
    "stale": {"claim_stale", "unknown"}, "spawn_failed": {"spawn_failed", "unknown"},
    "rate_limited": {"rate_limited", "unknown"}, "gave_up": {"retry_exhausted", "unknown"},
    "workspace_deferred": {"workspace_deferred", "unknown"},
    "watchdog_blocked": {"policy_hold", "unknown"}, "watchdog_deferred": {"policy_hold", "unknown"},
    "routed_to_repair_profile": {"repair_routed", "unknown"}, "scheduled": {"scheduled", "unknown"},
}


class ContractError(ValueError):
    """Content-free contract rejection; never echoes input values."""


def _require(condition: bool, field: str) -> None:
    if not condition:
        raise ContractError(f"Invalid coordination contract: {field}")


def _object(value: Any, required: frozenset[str], field: str, optional=frozenset()) -> Mapping:
    _require(isinstance(value, Mapping), field)
    _require(required <= value.keys() <= required | optional, field)
    return value


def _integer(value: Any, field: str, *, minimum: int = 0, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    _require(type(value) is int and minimum <= value <= _MAX_INTEGER, field)


def _reference(value: Any, field: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    _require(isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None, field)


def _scope(value: Any, field: str) -> None:
    _require(isinstance(value, str), field)
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise ContractError(f"Invalid coordination contract: {field}") from None
    _require(canonical == value, field)


def _nullable_object(value: Any, fields: frozenset[str], field: str) -> Mapping | None:
    return None if value is None else _object(value, fields, field)


def _repository(value: Any) -> None:
    row = _nullable_object(value, frozenset({"repository_id", "repository", "pr_number", "target_head", "base_sha", "execution_sha", "merge_parent_shas"}), "repository_ref")
    if row is None:
        return
    _integer(row["repository_id"], "repository_ref.repository_id", minimum=1)
    _integer(row["pr_number"], "repository_ref.pr_number", minimum=1, nullable=True)
    name = row["repository"]
    _require(isinstance(name, str) and len(name.split("/")) == 2, "repository_ref.repository")
    for part in name.split("/"):
        _reference(part, "repository_ref.repository")
    head = row["target_head"]
    _require(isinstance(head, str) and _SHA.fullmatch(head) is not None, "repository_ref.target_head")
    parents = row["merge_parent_shas"]
    _require(type(parents) is list and len(parents) <= 16, "repository_ref.merge_parent_shas")
    for sha in [row["base_sha"], row["execution_sha"], *parents]:
        if sha is not None:
            _require(isinstance(sha, str) and _SHA.fullmatch(sha) is not None and len(sha) == len(head), "repository_ref.sha")
    _require(all(sha is not None for sha in parents), "repository_ref.merge_parent_shas")


def _dependencies(record: Mapping) -> None:
    parents = record["dependency_parent_ids"]
    _require(type(parents) is list and len(parents) <= MAX_DEPENDENCY_REFS, "dependency_parent_ids")
    for parent in parents:
        _reference(parent, "dependency_parent_ids")
    _require(len(set(parents)) == len(parents) and record["task_id"] not in parents, "dependency_parent_ids")
    if _GRAPH_FIELDS & record.keys():
        _require(_GRAPH_FIELDS <= record.keys(), "dependency_graph_ref")
        _reference(record["dependency_graph_ref"], "dependency_graph_ref")
        _integer(record["dependency_count"], "dependency_count", minimum=MAX_DEPENDENCY_REFS + 1)
        _require(not parents, "dependency_parent_ids")


def _execution_identity(record: Mapping) -> None:
    parent = _nullable_object(record["parent_run_ref"], frozenset({"scope_id", "board", "task_id", "run_id"}), "parent_run_ref")
    if parent is not None:
        _scope(parent["scope_id"], "parent_run_ref.scope_id")
        for field in ("board", "task_id"):
            _reference(parent[field], f"parent_run_ref.{field}")
        _integer(parent["run_id"], "parent_run_ref.run_id", minimum=1)
    worker = _nullable_object(record["worker"], frozenset({"host_id", "instance_id", "process_start_id"}), "worker")
    if worker is not None:
        for field in ("host_id", "instance_id", "process_start_id"):
            _reference(worker[field], f"worker.{field}")
    profile = _nullable_object(record["profile"], frozenset({"scope_id", "name"}), "profile")
    if profile is not None:
        _scope(profile["scope_id"], "profile.scope_id")
        _reference(profile["name"], "profile.name")


def _source_identity(record: Mapping) -> None:
    source = _nullable_object(record["source_ref"], frozenset({"source", "id", "revision", "attempt"}), "source_ref")
    if source is not None:
        _require(source["source"] in ("controller", "worker", "webhook", "ci", "review"), "source_ref.source")
        for field in ("id", "revision"):
            _reference(source[field], f"source_ref.{field}")
        _integer(source["attempt"], "source_ref.attempt", minimum=1, nullable=True)
    cause = record["causation_id"]
    if cause is not None:
        _require(isinstance(cause, str), "causation_id")
        prefix, separator, seq = cause.rpartition(":")
        _require(separator == ":" and prefix == record["scope_id"] and seq.isascii() and seq.isdecimal(), "causation_id")
        _require(len(seq) <= 19 and str(int(seq)) == seq and 0 < int(seq) < record["seq"], "causation_id")


def _claimed(value: Any) -> None:
    row = _object(value, frozenset({"contract_revision", "claim_ref", "deadline"}), "payload")
    for field in ("contract_revision", "claim_ref"):
        _reference(row[field], f"payload.{field}")
    _integer(row["deadline"], "payload.deadline", nullable=True)


def _ended(value: Any) -> None:
    row = _object(value, frozenset({"outcome", "reason_code", "receipt_ref", "diagnostic_ref"}), "payload")
    outcome, reason = row["outcome"], row["reason_code"]
    _require(isinstance(outcome, str) and outcome in _END_REASONS, "payload.outcome")
    _require(isinstance(reason, str) and reason in _END_REASONS[outcome], "payload.reason_code")
    for field in ("receipt_ref", "diagnostic_ref"):
        _reference(row[field], f"payload.{field}", nullable=True)


_CONTEXT_FIELDS = frozenset({"contract_revision", "contract_digest", "context_revision", "session_ref"})
_COMPACTION_FIELDS = frozenset({"attempt_id", "outcome", "token_count_method", "tokens_before", "tokens_after"})


def _context(value: Any, *, compacted: bool = False) -> None:
    row = _object(value, _CONTEXT_FIELDS | (_COMPACTION_FIELDS if compacted else frozenset()), "payload")
    for field in ("contract_revision", "context_revision", "session_ref"):
        _reference(row[field], f"payload.{field}")
    digest = row["contract_digest"]
    _require(isinstance(digest, str) and _DIGEST.fullmatch(digest) is not None, "payload.contract_digest")
    if compacted:
        _reference(row["attempt_id"], "payload.attempt_id")
        _require(row["outcome"] in ("committed", "aborted", "failed", "skipped", "blocked"), "payload.outcome")
        method = row["token_count_method"]
        _require(method in ("measured", "estimate_rough", "unknown"), "payload.token_count_method")
        for field in ("tokens_before", "tokens_after"):
            _integer(row[field], f"payload.{field}", nullable=True)
        if method == "unknown":
            _require(row["tokens_before"] is None and row["tokens_after"] is None, "payload.token_count_method")


_PAYLOAD_VALIDATORS = {
    "task.claimed": _claimed, "run.ended": _ended,
    "context.bound": _context, "context.compacted": lambda value: _context(value, compacted=True),
}


def _plain_json(value: Any) -> Any:
    """Detach the validated mappings before encoding; Mapping is the public API."""
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_json(item) for item in value]
    return value


def validate_event(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a producer record and return detached JSON; grants no authority."""
    record = _object(record, _IDENTITY_FIELDS | _ENVELOPE_FIELDS, "envelope", _GRAPH_FIELDS)
    _require(record["schema"] == SCHEMA_NAME and record["version"] == SCHEMA_VERSION, "schema/version")
    event_type = record["type"]
    _require(isinstance(event_type, str) and event_type in _PAYLOAD_VALIDATORS, "type")
    _scope(record["scope_id"], "scope_id")
    for field in ("board", "task_id"):
        _reference(record[field], field)
    _reference(record["root_task_id"], "root_task_id", nullable=True)
    for field in ("seq", "run_id"):
        _integer(record[field], field, minimum=1)
    for field in ("created_at", "observed_at"):
        _integer(record[field], field)
    _require(record["event_id"] == f"{record['scope_id']}:{record['seq']}", "event_id")
    _dependencies(record)
    _execution_identity(record)
    _repository(record["repository_ref"])
    _source_identity(record)
    _PAYLOAD_VALIDATORS[event_type](record["payload"])
    encoded = json.dumps(_plain_json(record), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    _require(len(encoded.encode("utf-8")) <= MAX_EVENT_BYTES, "event_size")
    return json.loads(encoded)


def build_event(
    *, event_type: str, identity: Mapping[str, Any], seq: int,
    created_at: int, observed_at: int, payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build from an explicit committed store ID; no sequence allocation or I/O."""
    identity = _object(identity, _IDENTITY_FIELDS, "identity", _GRAPH_FIELDS)
    return validate_event({
        **identity, "schema": SCHEMA_NAME, "version": SCHEMA_VERSION, "type": event_type,
        "seq": seq, "event_id": f"{identity['scope_id']}:{seq}",
        "created_at": created_at, "observed_at": observed_at, "payload": payload,
    })
