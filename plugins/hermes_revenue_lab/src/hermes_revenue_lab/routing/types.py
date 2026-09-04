"""Immutable public types for HRL-2 policy and route evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Generic, Literal, Mapping, TypeVar


TIER_NAMES = ("no_llm", "fast", "standard", "reasoning", "coding", "escalation")
TierName = Literal["no_llm", "fast", "standard", "reasoning", "coding", "escalation"]
TierStatus = Literal["available", "unavailable"]
_ReceiptValue = TypeVar("_ReceiptValue")


@dataclass(frozen=True)
class TierPolicy:
    name: TierName
    status: TierStatus
    model: str | None
    model_digest: str | None
    thinking: bool | None
    reasoning: str | None
    permitted_during_luna: bool
    reason: str | None

    @classmethod
    def from_document(cls, name: str, value: Mapping[str, object]) -> "TierPolicy":
        expected = {
            "status",
            "model",
            "model_digest",
            "thinking",
            "reasoning",
            "permitted_during_luna",
            "reason",
        }
        if set(value) != expected:
            raise ValueError(f"tier {name} fields do not match the routing schema")
        if name not in TIER_NAMES:
            raise ValueError(f"unknown routing tier {name}")
        status = value["status"]
        if status not in ("available", "unavailable"):
            raise ValueError(f"tier {name} has invalid status")
        model = value["model"]
        digest = value["model_digest"]
        if status == "unavailable" and (model is not None or digest is not None):
            raise ValueError(f"unavailable tier {name} cannot contain a model")
        if status == "available" and name != "no_llm":
            if not isinstance(model, str) or not model:
                raise ValueError(f"available tier {name} requires a model")
            if not isinstance(digest, str) or not digest:
                raise ValueError(f"available tier {name} requires a model digest")
        if name == "no_llm" and (model is not None or digest is not None):
            raise ValueError("no_llm cannot contain a model")
        thinking = value["thinking"]
        if thinking is not None and not isinstance(thinking, bool):
            raise ValueError(f"tier {name} has invalid thinking control")
        reasoning = value["reasoning"]
        if reasoning is not None and reasoning not in ("low", "medium", "high"):
            raise ValueError(f"tier {name} has invalid reasoning control")
        permitted = value["permitted_during_luna"]
        if not isinstance(permitted, bool):
            raise ValueError(f"tier {name} has invalid Luna control")
        reason = value["reason"]
        if reason is not None and (not isinstance(reason, str) or not reason):
            raise ValueError(f"tier {name} has invalid availability reason")
        return cls(
            name=name,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            model=model if isinstance(model, str) else None,
            model_digest=digest if isinstance(digest, str) else None,
            thinking=thinking,
            reasoning=reasoning if isinstance(reasoning, str) else None,
            permitted_during_luna=permitted,
            reason=reason if isinstance(reason, str) else None,
        )

    def canonical_record(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("name")
        return value


@dataclass(frozen=True)
class RoutingPolicy:
    schema_version: str
    benchmark_id: str
    benchmark_sha256: str
    inventory_id: str
    selections_sha256: str
    tiers: Mapping[str, TierPolicy]

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> "RoutingPolicy":
        if set(document) != {"schema_version", "source", "tiers"}:
            raise ValueError("routing policy fields do not match the schema")
        if document["schema_version"] != "hrl.model_routing_policy.v1":
            raise ValueError("unsupported routing policy schema")
        source = document["source"]
        tiers_value = document["tiers"]
        if not isinstance(source, Mapping) or set(source) != {
            "benchmark_id",
            "benchmark_sha256",
            "inventory_id",
            "selections_sha256",
        }:
            raise ValueError("routing policy source binding is invalid")
        if not isinstance(tiers_value, Mapping) or set(tiers_value) != set(TIER_NAMES):
            raise ValueError("routing policy must contain the exact six tiers")
        for field in ("benchmark_id", "inventory_id"):
            if not isinstance(source[field], str) or not source[field]:
                raise ValueError(f"routing policy {field} is invalid")
        for field in ("benchmark_sha256", "selections_sha256"):
            value = source[field]
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"routing policy {field} is invalid")
            try:
                int(value, 16)
            except ValueError as exc:
                raise ValueError(f"routing policy {field} is invalid") from exc
        tiers: dict[str, TierPolicy] = {}
        for name in TIER_NAMES:
            value = tiers_value[name]
            if not isinstance(value, Mapping):
                raise ValueError(f"tier {name} must be an object")
            tiers[name] = TierPolicy.from_document(name, value)
        return cls(
            schema_version="hrl.model_routing_policy.v1",
            benchmark_id=str(source["benchmark_id"]),
            benchmark_sha256=str(source["benchmark_sha256"]),
            inventory_id=str(source["inventory_id"]),
            selections_sha256=str(source["selections_sha256"]),
            tiers=MappingProxyType(tiers),
        )


@dataclass(frozen=True)
class RouteDecision:
    requested_tier: TierName
    actual_tier: TierName
    actual_model: str | None
    model_digest: str | None
    thinking: bool | None
    reasoning: str | None
    escalation_reason: str | None


@dataclass(frozen=True)
class TaskExecutionReceipt(Generic[_ReceiptValue]):
    """Source-bound proof of which selected model produced an executor result."""

    value: _ReceiptValue
    actual_model: str | None
    model_digest: str | None


@dataclass(frozen=True)
class RoutingEvent:
    event_id: str
    task_id: str
    requested_tier: TierName
    actual_tier: TierName | None
    actual_model: str | None
    model_digest: str | None
    escalation_reason: str | None
    started_at: str
    ended_at: str
    wall_time_seconds: float
    task_result: str
    retries: int
    estimated_compute_cost: Mapping[str, object]
    success: bool

    def canonical_record(self) -> dict[str, object]:
        value = asdict(self)
        value["estimated_compute_cost"] = dict(self.estimated_compute_cost)
        return value
