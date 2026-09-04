"""Evidence-domain validation and coarse HRL-6 ranking."""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

from .types import (
    BANDS,
    DIMENSIONS,
    RANKING_FACTORS,
    DimensionScore,
    FactorRating,
    OpportunityAssessment,
    OpportunityCandidate,
)


_BAND_VALUE = {band: index for index, band in enumerate(BANDS, start=1)}
_DIMENSION_FIELDS = {
    "demand": {"problem", "current_workaround", "urgency", "frequency", "willingness_to_pay"},
    "monetizability": {
        "willingness_to_pay",
        "market_size_proxy",
        "recurring_revenue_potential",
    },
    "automation": {
        "data_availability",
        "automation_percentage",
        "human_effort",
        "technical_difficulty",
    },
    "competition": {"competition", "existing_products"},
    "defensibility": {"moat", "competition", "existing_products"},
    "cost": {"startup_cost", "recurring_cost", "technical_difficulty"},
    "risk": {"platform_dependency", "policy_risk"},
    "time_to_revenue": {"time_to_first_revenue", "distribution_channel", "startup_cost"},
}
_FACTOR_FIELDS = {
    "expected_value": {"willingness_to_pay", "market_size_proxy"},
    "automation": {"automation_percentage", "data_availability"},
    "recurrence": {"recurring_revenue_potential"},
    "defensibility": {"moat"},
    "human_labor": {"human_effort"},
    "capital_required": {"startup_cost", "recurring_cost"},
    "platform_risk": {"platform_dependency", "policy_risk"},
}


def _validate_exact(items: Sequence[object], names: tuple[str, ...], attribute: str) -> None:
    observed = tuple(getattr(item, attribute) for item in items)
    if len(observed) != len(set(observed)) or set(observed) != set(names):
        raise ValueError(f"assessment requires every {attribute} exactly once")


def _validate_references(
    candidate: OpportunityCandidate,
    items: Sequence[object],
    name_attribute: str,
    allowed_fields: dict[str, set[str]],
) -> None:
    evidence = {item.evidence_id: item for item in candidate.evidence}
    for scored in items:
        name = getattr(scored, name_attribute)
        for evidence_id in scored.evidence_ids:
            item = evidence.get(evidence_id)
            if item is None:
                raise ValueError("assessment evidence reference is missing")
            if item.field_name not in allowed_fields[name]:
                raise ValueError("assessment evidence is not relevant to its score")


def _ranking_fraction(factors: Sequence[FactorRating]) -> Fraction:
    values = {item.factor: _BAND_VALUE[item.band] for item in factors}
    numerator = (
        values["expected_value"]
        * values["automation"]
        * values["recurrence"]
        * values["defensibility"]
    )
    denominator = (
        values["human_labor"] * values["capital_required"] * values["platform_risk"]
    )
    return Fraction(numerator, denominator)


def _tier(value: Fraction) -> str:
    if value >= 25:
        return "A"
    if value >= 8:
        return "B"
    if value >= 2:
        return "C"
    if value >= Fraction(1, 2):
        return "D"
    return "E"


def build_assessment(
    candidate: OpportunityCandidate,
    scores: tuple[DimensionScore, ...],
    factors: tuple[FactorRating, ...],
) -> OpportunityAssessment:
    _validate_exact(scores, DIMENSIONS, "dimension")
    _validate_exact(factors, RANKING_FACTORS, "factor")
    _validate_references(candidate, scores, "dimension", _DIMENSION_FIELDS)
    _validate_references(candidate, factors, "factor", _FACTOR_FIELDS)
    return OpportunityAssessment(candidate, scores, factors, _tier(_ranking_fraction(factors)))


def rank_assessments(assessments: Sequence[OpportunityAssessment]) -> tuple[str, ...]:
    ranked = sorted(
        assessments,
        key=lambda item: (-_ranking_fraction(item.factors), item.candidate.opportunity_id),
    )
    return tuple(item.candidate.opportunity_id for item in ranked)
