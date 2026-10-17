"""Wire-safe records shared by federated Hermes runners.

The protocol deliberately contains task requirements and runner capabilities,
not credentials or profile state.  A runner advertises what it can do; the
coordinator remains responsible for choosing an eligible runner.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping


PROTOCOL_SCHEMA = "hermes.fleet.v1"
MAX_MESSAGE_BYTES = 1024 * 1024
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ProtocolError(ValueError):
    """Raised when a fleet message is malformed or unsafe to process."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{field} must be a non-empty string")
    return value


def _normalized_values(values: Any, field: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise ProtocolError(f"{field} must be a list of strings")
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ProtocolError(f"{field} must contain non-empty strings")
        result.add(value)
    return tuple(sorted(result))


def _body_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    body = value.get("body")
    return body if isinstance(body, Mapping) else value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"telemetry {field} must be a nonnegative integer")
    return value


def _optional_nonnegative_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field)


def _optional_finite_nonnegative_float(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"telemetry {field} must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ProtocolError(f"telemetry {field} must be a finite nonnegative number")
    return result


@dataclass(frozen=True)
class TaskTelemetry:
    """Measured output-token performance for one completed federated task."""

    output_tokens: int
    duration_ms: int
    output_tps: float | None = None

    def __post_init__(self) -> None:
        output_tokens = _nonnegative_int(self.output_tokens, "output_tokens")
        duration_ms = _nonnegative_int(self.duration_ms, "duration_ms")
        output_tps = _optional_finite_nonnegative_float(self.output_tps, "output_tps")
        if output_tps is None and output_tokens > 0 and duration_ms > 0:
            output_tps = output_tokens / (duration_ms / 1000)
        object.__setattr__(self, "output_tokens", output_tokens)
        object.__setattr__(self, "duration_ms", duration_ms)
        object.__setattr__(self, "output_tps", output_tps)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "output_tokens": self.output_tokens,
            "duration_ms": self.duration_ms,
        }
        if self.output_tps is not None:
            result["output_tps"] = self.output_tps
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TaskTelemetry:
        if not isinstance(value, Mapping):
            raise ProtocolError("telemetry must be an object")
        return cls(
            output_tokens=value.get("output_tokens", 0),
            duration_ms=value.get("duration_ms", 0),
            output_tps=value.get("output_tps"),
        )


@dataclass(frozen=True)
class RunnerTelemetry:
    """Last completed-task metric retained on a runner's status row."""

    last_output_tokens: int | None = None
    last_output_duration_ms: int | None = None
    last_output_tps: float | None = None
    metrics_updated_at: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "last_output_tokens", _optional_nonnegative_int(self.last_output_tokens, "last_output_tokens"))
        object.__setattr__(self, "last_output_duration_ms", _optional_nonnegative_int(self.last_output_duration_ms, "last_output_duration_ms"))
        object.__setattr__(self, "last_output_tps", _optional_finite_nonnegative_float(self.last_output_tps, "last_output_tps"))
        object.__setattr__(self, "metrics_updated_at", _optional_finite_nonnegative_float(self.metrics_updated_at, "metrics_updated_at"))

    @classmethod
    def from_task(cls, telemetry: TaskTelemetry, *, updated_at: float) -> RunnerTelemetry:
        return cls(
            last_output_tokens=telemetry.output_tokens,
            last_output_duration_ms=telemetry.duration_ms,
            last_output_tps=telemetry.output_tps,
            metrics_updated_at=updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "last_output_tokens": self.last_output_tokens,
                "last_output_duration_ms": self.last_output_duration_ms,
                "last_output_tps": self.last_output_tps,
                "metrics_updated_at": self.metrics_updated_at,
            }.items()
            if value is not None
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunnerTelemetry:
        if not isinstance(value, Mapping):
            raise ProtocolError("telemetry must be an object")
        return cls(
            last_output_tokens=value.get("last_output_tokens"),
            last_output_duration_ms=value.get("last_output_duration_ms"),
            last_output_tps=value.get("last_output_tps"),
            metrics_updated_at=value.get("metrics_updated_at"),
        )


@dataclass(frozen=True)
class TaskRequirement:
    models: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    project: str | None = None
    workspace_kind: str | None = None

    _KIND: ClassVar[str] = "task_requirement"

    def __post_init__(self) -> None:
        object.__setattr__(self, "models", _normalized_values(self.models, "models"))
        object.__setattr__(self, "tools", _normalized_values(self.tools, "tools"))
        if self.project is not None:
            _required_text(self.project, "project")
        if self.workspace_kind is not None:
            _required_text(self.workspace_kind, "workspace_kind")

    def to_dict(self) -> dict[str, Any]:
        return {
            "models": list(self.models),
            "tools": list(self.tools),
            "project": self.project,
            "workspace_kind": self.workspace_kind,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TaskRequirement:
        if not isinstance(value, Mapping):
            raise ProtocolError("requirement must be an object")
        value = _body_mapping(value)
        return cls(
            models=value.get("models", ()),
            tools=value.get("tools", ()),
            project=value.get("project"),
            workspace_kind=value.get("workspace_kind"),
        )


@dataclass(frozen=True)
class RunnerCapability:
    node_id: str
    profile: str
    models: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    platform: str = ""

    _KIND: ClassVar[str] = "runner_capability"

    def __post_init__(self) -> None:
        _required_text(self.node_id, "node_id")
        _required_text(self.profile, "profile")
        _required_text(self.platform, "platform")
        object.__setattr__(self, "models", _normalized_values(self.models, "models"))
        object.__setattr__(self, "tools", _normalized_values(self.tools, "tools"))
        object.__setattr__(self, "projects", _normalized_values(self.projects, "projects"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "profile": self.profile,
            "models": list(self.models),
            "tools": list(self.tools),
            "projects": list(self.projects),
            "platform": self.platform,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunnerCapability:
        if not isinstance(value, Mapping):
            raise ProtocolError("runner capability must be an object")
        value = _body_mapping(value)
        return cls(
            node_id=_required_text(value.get("node_id"), "node_id"),
            profile=_required_text(value.get("profile"), "profile"),
            models=value.get("models", ()),
            tools=value.get("tools", ()),
            projects=value.get("projects", ()),
            platform=_required_text(value.get("platform"), "platform"),
        )


@dataclass(frozen=True)
class FleetTask:
    task_id: str
    title: str
    body: str
    requirement: TaskRequirement
    idempotency_key: str

    _KIND: ClassVar[str] = "fleet_task"

    def __post_init__(self) -> None:
        _required_text(self.task_id, "task_id")
        _required_text(self.title, "title")
        _required_text(self.body, "body")
        if not isinstance(self.requirement, TaskRequirement):
            raise ProtocolError("requirement must be a TaskRequirement")
        if not isinstance(self.idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(
            self.idempotency_key
        ):
            raise ValueError("idempotency_key must contain only safe identifier characters")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "body": self.body,
            "requirement": self.requirement.to_dict(),
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FleetTask:
        if not isinstance(value, Mapping):
            raise ProtocolError("fleet task must be an object")
        value = _body_mapping(value)
        return cls(
            task_id=_required_text(value.get("task_id"), "task_id"),
            title=_required_text(value.get("title"), "title"),
            body=_required_text(value.get("body"), "body"),
            requirement=TaskRequirement.from_dict(value.get("requirement", {})),
            idempotency_key=value.get("idempotency_key", ""),
        )


_MESSAGE_TYPES = {
    TaskRequirement._KIND: TaskRequirement,
    RunnerCapability._KIND: RunnerCapability,
    FleetTask._KIND: FleetTask,
}


def _message_value(value: Any) -> tuple[str, Mapping[str, Any]]:
    for kind, message_type in _MESSAGE_TYPES.items():
        if isinstance(value, message_type):
            return kind, value.to_dict()
    raise ProtocolError(f"unsupported fleet message type: {type(value).__name__}")


def encode_message(value: Any) -> bytes:
    kind, body = _message_value(value)
    payload = json.dumps(
        {"schema": PROTOCOL_SCHEMA, "kind": kind, "body": body},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message too large")
    return payload


def decode_message(payload: bytes, *, expected_kind: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, (bytes, bytearray)):
        raise ProtocolError("message payload must be bytes")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message too large")
    try:
        message = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid fleet message JSON") from exc
    if not isinstance(message, dict):
        raise ProtocolError("fleet message must be an object")
    if message.get("schema") != PROTOCOL_SCHEMA:
        raise ProtocolError(f"unsupported fleet protocol schema: {message.get('schema')!r}")
    kind = message.get("kind")
    if kind not in _MESSAGE_TYPES:
        raise ProtocolError(f"unsupported fleet message kind: {kind!r}")
    if expected_kind is not None and kind != expected_kind:
        raise ProtocolError(f"expected {expected_kind}, received {kind}")
    body = message.get("body")
    if not isinstance(body, dict):
        raise ProtocolError("fleet message body must be an object")
    message["body"] = body
    return message


CLAIM_RECOVERY_VERSION = "hermes.fleet.claim-recovery.v1"
# GET reconciliation must fit the stdlib server request-line limit even after
# worst-case URL quoting (three bytes per canonical JSON byte).
MAX_CLAIM_REQUEST_BYTES = 16_384
_HEX_ID = re.compile(r"^[0-9a-f]{64}$")


def recovery_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _HEX_ID.fullmatch(value):
        raise ProtocolError(f"invalid {field}")
    return value


@dataclass(frozen=True)
class ClaimRequest:
    version: str
    store_id: str
    cutover_id: str
    installation_id: str
    nonce: str
    capability: RunnerCapability
    now: float | None = None
    lease_seconds: float = 60.0

    def __post_init__(self):
        if self.version != CLAIM_RECOVERY_VERSION:
            raise ProtocolError("unsupported claim recovery version")
        for field in ("store_id", "cutover_id", "installation_id", "nonce"):
            recovery_id(getattr(self, field), field)
        if not isinstance(self.capability, RunnerCapability):
            raise ProtocolError("invalid request capability")
        if self.now is not None:
            _optional_finite_nonnegative_float(self.now, "now")
        lease = _optional_finite_nonnegative_float(self.lease_seconds, "lease_seconds")
        if lease is None or not 0 < lease <= 86400:
            raise ProtocolError("invalid lease_seconds")
        if len(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) > MAX_CLAIM_REQUEST_BYTES:
            raise ProtocolError("claim request too large for exact GET reconciliation")

    def to_dict(self):
        return {"version": self.version, "store_id": self.store_id,
                "cutover_id": self.cutover_id, "installation_id": self.installation_id,
                "nonce": self.nonce, "capability": self.capability.to_dict(),
                "now": self.now, "lease_seconds": self.lease_seconds}

    @property
    def fingerprint(self):
        import hashlib
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    @classmethod
    def from_dict(cls, value):
        fields = {"version", "store_id", "cutover_id", "installation_id", "nonce",
                  "capability", "now", "lease_seconds"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ProtocolError("incomplete claim request")
        return cls(**{**value, "capability": RunnerCapability.from_dict(value["capability"])})


@dataclass(frozen=True)
class ClaimOutcome:
    request: ClaimRequest
    kind: str
    claim: dict[str, Any] | None = None

    def __post_init__(self):
        if self.kind not in {"absent", "claimed", "empty", "sealed_empty"}:
            raise ProtocolError("invalid claim outcome")
        if self.kind != "claimed":
            if self.claim is not None:
                raise ProtocolError("non-claim outcome contains claim")
            return
        fields = {"task_id", "claim_id", "node_id", "runner_profile", "lease_expires_at", "attempt"}
        if not isinstance(self.claim, dict) or set(self.claim) != fields:
            raise ProtocolError("incomplete recovered claim")
        for field in ("task_id", "claim_id", "node_id", "runner_profile"):
            _required_text(self.claim[field], field)
        if (self.claim["node_id"] != self.request.capability.node_id
                or self.claim["runner_profile"] != self.request.capability.profile):
            raise ProtocolError("recovered claim owner mismatch")
        if _nonnegative_int(self.claim["attempt"], "attempt") < 1:
            raise ProtocolError("invalid recovered attempt")
        if _optional_finite_nonnegative_float(self.claim["lease_expires_at"], "lease") is None:
            raise ProtocolError("missing recovered lease")

    def to_dict(self):
        return {"version": CLAIM_RECOVERY_VERSION, "request": self.request.to_dict(),
                "fingerprint": self.request.fingerprint, "kind": self.kind, "claim": self.claim}

    @classmethod
    def from_dict(cls, value, *, request):
        if (not isinstance(value, dict)
                or set(value) != {"version", "request", "fingerprint", "kind", "claim"}
                or value["version"] != CLAIM_RECOVERY_VERSION):
            raise ProtocolError("incomplete claim outcome")
        binding = ClaimRequest.from_dict(value["request"])
        if binding != request or value["fingerprint"] != request.fingerprint:
            raise ProtocolError("claim request binding mismatch")
        return cls(request, value["kind"], value["claim"])


def validate_recovery_capabilities(value):
    if (not isinstance(value, dict)
            or set(value) != {"version", "store_id", "cutover", "resolve_or_seal"}
            or value["version"] != CLAIM_RECOVERY_VERSION
            or value["resolve_or_seal"] is not True):
        raise ProtocolError("unsupported claim recovery capability")
    recovery_id(value["store_id"], "store_id")
    receipt = value["cutover"]
    if receipt is not None:
        if (not isinstance(receipt, dict)
                or set(receipt) != {"version", "store_id", "cutover_id", "scope", "legacy_claims"}
                or receipt["version"] != CLAIM_RECOVERY_VERSION
                or receipt["store_id"] != value["store_id"]
                or receipt["scope"] != "store-wide"
                or receipt["legacy_claims"] != "permanently_closed"):
            raise ProtocolError("invalid legacy cutover receipt")
        recovery_id(receipt["cutover_id"], "cutover_id")
    return value
