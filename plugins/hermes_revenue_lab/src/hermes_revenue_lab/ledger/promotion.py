"""Evidence-bound deterministic experiment promotion ladder."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from .metrics import derive_metrics
from .types import ExperimentRecord, PromotionEvidence


def _values(evidence: Sequence[PromotionEvidence], kind: str) -> tuple[Decimal, ...]:
    return tuple(item.value for item in evidence if item.kind == kind and item.value is not None)


def highest_promotion_stage(
    record: ExperimentRecord,
    evidence: Sequence[PromotionEvidence],
) -> str:
    stage = "E0"
    if not any(item.kind == "market_test_live" for item in evidence):
        return stage
    stage = "E1"
    stranger_dollar = any(
        item.kind == "legitimate_customer_payment"
        and item.customer_relationship == "stranger"
        and item.value is not None
        and item.value > 0
        for item in evidence
    )
    if not stranger_dollar:
        return stage
    stage = "E2"
    if record.gross_revenue is None or record.gross_revenue < Decimal("50"):
        return stage
    stage = "E3"
    metrics = derive_metrics(record)
    if (
        record.gross_revenue < Decimal("100")
        or metrics.contribution_profit is None
        or metrics.contribution_profit <= 0
    ):
        return stage
    stage = "E4"
    monthly_revenue = _values(evidence, "monthly_revenue")
    monthly_labor = _values(evidence, "monthly_human_minutes")
    if not monthly_revenue or max(monthly_revenue) < Decimal("250"):
        return stage
    if not monthly_labor or min(monthly_labor) >= Decimal("120"):
        return stage
    stage = "E5"
    recurring = _values(evidence, "monthly_recurring_revenue")
    if not recurring or max(recurring) < Decimal("500"):
        return stage
    stage = "E6"
    stable = _values(evidence, "stable_unit_economics")
    if not stable or max(stable) <= 0:
        return stage
    return "E7"
