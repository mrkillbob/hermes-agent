"""Immutable raw-evidence and ordinal scoring types for HRL-6."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from hermes_revenue_lab.ledger.types import parse_timestamp


OPPORTUNITY_FIELDS = (
    "customer",
    "problem",
    "current_workaround",
    "urgency",
    "frequency",
    "willingness_to_pay",
    "market_size_proxy",
    "competition",
    "existing_products",
    "distribution_channel",
    "data_availability",
    "automation_percentage",
    "human_effort",
    "startup_cost",
    "recurring_cost",
    "platform_dependency",
    "policy_risk",
    "technical_difficulty",
    "time_to_first_revenue",
    "moat",
    "recurring_revenue_potential",
)
DIMENSIONS = (
    "demand",
    "monetizability",
    "automation",
    "competition",
    "defensibility",
    "cost",
    "risk",
    "time_to_revenue",
)
RANKING_FACTORS = (
    "expected_value",
    "automation",
    "recurrence",
    "defensibility",
    "human_labor",
    "capital_required",
    "platform_risk",
)
BANDS = ("very_low", "low", "medium", "high", "very_high")
Band = Literal["very_low", "low", "medium", "high", "very_high"]
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is invalid")


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    field_name: str
    statement: str
    source_ref: str
    observed_at: str

    def __post_init__(self) -> None:
        _identifier("evidence_id", self.evidence_id)
        if self.field_name not in OPPORTUNITY_FIELDS:
            raise ValueError("evidence field is invalid")
        if not isinstance(self.statement, str) or not 1 <= len(self.statement) <= 2_000:
            raise ValueError("evidence statement is invalid")
        if not isinstance(self.source_ref, str) or not 1 <= len(self.source_ref) <= 1_000:
            raise ValueError("evidence source_ref is invalid")
        parse_timestamp(self.observed_at)


ObservationStatus = Literal["observed", "unavailable"]


@dataclass(frozen=True)
class ObservedField:
    field_name: str
    status: ObservationStatus
    value: str | Decimal | None
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.field_name not in OPPORTUNITY_FIELDS:
            raise ValueError("opportunity field is invalid")
        if self.status not in {"observed", "unavailable"}:
            raise ValueError("opportunity observation status is invalid")
        if not self.evidence_ids:
            raise ValueError("opportunity field requires raw evidence")
        for evidence_id in self.evidence_ids:
            _identifier("evidence reference", evidence_id)
        if self.status == "unavailable" and self.value is not None:
            raise ValueError("unavailable opportunity field value must remain None")
        if self.status == "observed":
            if isinstance(self.value, Decimal):
                if not self.value.is_finite():
                    raise ValueError("observed Decimal must be finite")
            elif not isinstance(self.value, str) or not 1 <= len(self.value) <= 2_000:
                raise ValueError("observed opportunity value is invalid")


@dataclass(frozen=True)
class OpportunityCandidate:
    opportunity_id: str
    fields: tuple[ObservedField, ...]
    evidence: tuple[EvidenceItem, ...]

    def __post_init__(self) -> None:
        _identifier("opportunity_id", self.opportunity_id)
        field_names = tuple(item.field_name for item in self.fields)
        if len(field_names) != len(set(field_names)) or set(field_names) != set(
            OPPORTUNITY_FIELDS
        ):
            raise ValueError("candidate must contain the complete opportunity schema")
        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("candidate evidence IDs must be unique")
        for field in self.fields:
            for evidence_id in field.evidence_ids:
                item = evidence_by_id.get(evidence_id)
                if item is None or item.field_name != field.field_name:
                    raise ValueError("opportunity evidence reference is missing or mismatched")


@dataclass(frozen=True)
class DimensionScore:
    dimension: str
    band: Band
    evidence_ids: tuple[str, ...]
    rationale_code: str

    def __post_init__(self) -> None:
        if self.dimension not in DIMENSIONS:
            raise ValueError("score dimension is invalid")
        if self.band not in BANDS:
            raise ValueError("score must use an ordinal band")
        if not self.evidence_ids:
            raise ValueError("score requires raw evidence")
        _identifier("rationale_code", self.rationale_code)


@dataclass(frozen=True)
class FactorRating:
    factor: str
    band: Band
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.factor not in RANKING_FACTORS:
            raise ValueError("ranking factor is invalid")
        if self.band not in BANDS:
            raise ValueError("factor must use an ordinal band")
        if not self.evidence_ids:
            raise ValueError("ranking factor requires raw evidence")


@dataclass(frozen=True)
class OpportunityAssessment:
    candidate: OpportunityCandidate
    scores: tuple[DimensionScore, ...]
    factors: tuple[FactorRating, ...]
    ranking_tier: Literal["A", "B", "C", "D", "E"]
