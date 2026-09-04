"""Strict read-only projection types for the HRL-16 revenue dashboard."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal

from hermes_revenue_lab.ledger.types import parse_timestamp

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _optional_decimal(name: str, value: object) -> None:
    if value is not None and (
        not isinstance(value, Decimal) or not value.is_finite() or value < 0
    ):
        raise ValueError(f"dashboard {name} must be nonnegative Decimal or unavailable")


def _optional_signed_decimal(name: str, value: object) -> None:
    if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
        raise ValueError(f"dashboard {name} must be finite Decimal or unavailable")


def _optional_count(name: str, value: object) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError(f"dashboard {name} must be nonnegative integer or unavailable")


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


@dataclass(frozen=True)
class TodayMetrics:
    revenue: Decimal | None = None
    expenses: Decimal | None = None
    profit: Decimal | None = None
    customers: int | None = None
    compute_hours: Decimal | None = None
    human_intervention_minutes: Decimal | None = None

    def __post_init__(self) -> None:
        for name in (
            "revenue",
            "expenses",
            "compute_hours",
            "human_intervention_minutes",
        ):
            _optional_decimal(name, getattr(self, name))
        _optional_signed_decimal("profit", self.profit)
        _optional_count("customers", self.customers)
        if None not in (self.revenue, self.expenses, self.profit) and (
            self.profit != self.revenue - self.expenses  # type: ignore[operator]
        ):
            raise ValueError(
                "dashboard profit does not reconcile with revenue and expenses"
            )

    def canonical_record(self) -> dict[str, object]:
        return {
            name: str(value) if isinstance(value, Decimal) else value
            for name, value in asdict(self).items()
        }

    @classmethod
    def from_document(cls, value: Mapping[str, object]) -> TodayMetrics:
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise ValueError("dashboard today schema is invalid")
        parsed = dict(value)
        for name in expected - {"customers"}:
            parsed[name] = _decimal(parsed[name])
        return cls(**parsed)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ExperimentCounts:
    researching: int | None = None
    testing: int | None = None
    profitable: int | None = None
    scaling: int | None = None
    killed: int | None = None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _optional_count(name, getattr(self, name))

    def canonical_record(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_document(cls, value: Mapping[str, object]) -> ExperimentCounts:
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("dashboard experiment schema is invalid")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class ModelEconomics:
    model: str
    invocations: int
    median_latency_seconds: Decimal | None
    success_rate: Decimal | None
    escalation_rate: Decimal | None
    compute_hours: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not _IDENTIFIER.fullmatch(self.model):
            raise ValueError("dashboard model identity is invalid")
        _optional_count("invocations", self.invocations)
        for name in ("median_latency_seconds", "compute_hours"):
            _optional_decimal(name, getattr(self, name))
        for name in ("success_rate", "escalation_rate"):
            value = getattr(self, name)
            _optional_decimal(name, value)
            if value is not None and not Decimal(0) <= value <= Decimal(1):
                label = name.replace("_", " ")
                raise ValueError(f"dashboard {label} must be between zero and one")

    def canonical_record(self) -> dict[str, object]:
        return {
            name: str(value) if isinstance(value, Decimal) else value
            for name, value in asdict(self).items()
        }

    @classmethod
    def from_document(cls, value: Mapping[str, object]) -> ModelEconomics:
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("dashboard model economics schema is invalid")
        parsed = dict(value)
        for name in (
            "median_latency_seconds",
            "success_rate",
            "escalation_rate",
            "compute_hours",
        ):
            parsed[name] = _decimal(parsed[name])
        return cls(**parsed)  # type: ignore[arg-type]


@dataclass(frozen=True)
class GuardResourceState:
    load_1m: float | None = None
    cpu_count: int | None = None
    memory_free_percent: float | None = None
    swap_used_bytes: int | None = None
    foreign_ollama_model_count: int | None = None
    luna_health_status: str = "unavailable"

    def __post_init__(self) -> None:
        for name in ("load_1m", "memory_free_percent"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"dashboard guard {name} is invalid")
        if self.load_1m is not None and self.load_1m < 0:
            raise ValueError("dashboard guard load is invalid")
        if (
            self.memory_free_percent is not None
            and not 0 <= self.memory_free_percent <= 100
        ):
            raise ValueError("dashboard guard memory percentage is invalid")
        for name in ("cpu_count", "swap_used_bytes", "foreign_ollama_model_count"):
            _optional_count(name, getattr(self, name))
        if self.luna_health_status not in {"healthy", "unhealthy", "unavailable"}:
            raise ValueError("dashboard Luna health is invalid")

    def canonical_record(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_document(cls, value: Mapping[str, object]) -> GuardResourceState:
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("dashboard guard resource schema is invalid")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class GuardPanel:
    state: Literal["FULL", "LIMITED", "PAUSED", "EMERGENCY_STOP", "unavailable"]
    reasons: tuple[str, ...]
    last_transition: str | None
    resources: GuardResourceState

    def __post_init__(self) -> None:
        if self.state not in {
            "FULL",
            "LIMITED",
            "PAUSED",
            "EMERGENCY_STOP",
            "unavailable",
        }:
            raise ValueError("dashboard guard state is invalid")
        if any(
            not isinstance(reason, str) or not 1 <= len(reason) <= 256
            for reason in self.reasons
        ):
            raise ValueError("dashboard guard reason is invalid")
        if self.last_transition is not None:
            parse_timestamp(self.last_transition)
        if self.state == "unavailable" and self.last_transition is not None:
            raise ValueError("unavailable guard cannot claim a transition")

    @classmethod
    def unavailable(cls) -> GuardPanel:
        return cls(
            "unavailable", ("guard_evidence_unavailable",), None, GuardResourceState()
        )

    def canonical_record(self) -> dict[str, object]:
        return {
            "state": self.state,
            "reasons": list(self.reasons),
            "last_transition": self.last_transition,
            "resources": self.resources.canonical_record(),
        }

    @classmethod
    def from_document(cls, value: Mapping[str, object]) -> GuardPanel:
        if set(value) != {"state", "reasons", "last_transition", "resources"}:
            raise ValueError("dashboard guard schema is invalid")
        resources = value["resources"]
        if not isinstance(resources, Mapping) or not isinstance(value["reasons"], list):
            raise TypeError("dashboard guard collections are invalid")
        return cls(
            state=value["state"],  # type: ignore[arg-type]
            reasons=tuple(value["reasons"]),  # type: ignore[arg-type]
            last_transition=value["last_transition"],  # type: ignore[arg-type]
            resources=GuardResourceState.from_document(resources),
        )


@dataclass(frozen=True)
class OpportunityQueueItem:
    candidate_id: str
    score: str
    evidence_count: int
    proposed_experiment: str
    required_approval: str

    def __post_init__(self) -> None:
        for name in ("candidate_id", "proposed_experiment", "required_approval"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"dashboard opportunity {name} is invalid")
        if self.score not in {"A", "B", "C", "D", "E"}:
            raise ValueError("dashboard opportunity score is invalid")
        _optional_count("evidence_count", self.evidence_count)

    def canonical_record(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_document(cls, value: Mapping[str, object]) -> OpportunityQueueItem:
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("dashboard opportunity schema is invalid")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: str
    freshness: Literal["current", "partial", "unavailable"]
    source_reasons: tuple[str, ...]
    today: TodayMetrics
    experiments: ExperimentCounts
    model_economics: tuple[ModelEconomics, ...]
    guard: GuardPanel
    opportunity_queue: tuple[OpportunityQueueItem, ...]

    def __post_init__(self) -> None:
        parse_timestamp(self.generated_at)
        if self.freshness not in {"current", "partial", "unavailable"}:
            raise ValueError("dashboard freshness is invalid")
        if any(
            not isinstance(reason, str) or not 1 <= len(reason) <= 256
            for reason in self.source_reasons
        ):
            raise ValueError("dashboard source reason is invalid")
        if self.freshness != "current" and not self.source_reasons:
            raise ValueError("non-current dashboard requires source reasons")
        models = tuple(item.model for item in self.model_economics)
        candidates = tuple(item.candidate_id for item in self.opportunity_queue)
        if len(models) != len(set(models)) or len(candidates) != len(set(candidates)):
            raise ValueError("dashboard row identities must be unique")

    @classmethod
    def unavailable(
        cls, *, generated_at: str, reasons: tuple[str, ...]
    ) -> DashboardSnapshot:
        return cls(
            generated_at,
            "unavailable",
            reasons,
            TodayMetrics(),
            ExperimentCounts(),
            (),
            GuardPanel.unavailable(),
            (),
        )

    def canonical_record(self) -> dict[str, object]:
        return {
            "schema_version": "hrl.dashboard_snapshot.v1",
            "generated_at": self.generated_at,
            "freshness": self.freshness,
            "source_reasons": list(self.source_reasons),
            "today": self.today.canonical_record(),
            "experiments": self.experiments.canonical_record(),
            "model_economics": [
                item.canonical_record() for item in self.model_economics
            ],
            "guard": self.guard.canonical_record(),
            "opportunity_queue": [
                item.canonical_record() for item in self.opportunity_queue
            ],
        }

    @classmethod
    def from_document(cls, value: Mapping[str, object]) -> DashboardSnapshot:
        expected = {
            "schema_version",
            "generated_at",
            "freshness",
            "source_reasons",
            "today",
            "experiments",
            "model_economics",
            "guard",
            "opportunity_queue",
        }
        if (
            set(value) != expected
            or value.get("schema_version") != "hrl.dashboard_snapshot.v1"
        ):
            raise ValueError("dashboard snapshot schema is invalid")
        if not all(
            isinstance(value[name], Mapping)
            for name in ("today", "experiments", "guard")
        ) or not all(
            isinstance(value[name], list)
            for name in ("source_reasons", "model_economics", "opportunity_queue")
        ):
            raise TypeError("dashboard snapshot collections are invalid")
        return cls(
            generated_at=value["generated_at"],  # type: ignore[arg-type]
            freshness=value["freshness"],  # type: ignore[arg-type]
            source_reasons=tuple(value["source_reasons"]),  # type: ignore[arg-type]
            today=TodayMetrics.from_document(value["today"]),  # type: ignore[arg-type]
            experiments=ExperimentCounts.from_document(value["experiments"]),  # type: ignore[arg-type]
            model_economics=tuple(
                ModelEconomics.from_document(item) for item in value["model_economics"]
            ),  # type: ignore[arg-type]
            guard=GuardPanel.from_document(value["guard"]),  # type: ignore[arg-type]
            opportunity_queue=tuple(
                OpportunityQueueItem.from_document(item)
                for item in value["opportunity_queue"]  # type: ignore[arg-type]
            ),
        )
