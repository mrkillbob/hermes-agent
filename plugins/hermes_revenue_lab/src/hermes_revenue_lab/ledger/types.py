"""Immutable HRL-5 ledger boundary types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
MONEY_FIELDS = (
    "gross_revenue",
    "refunds",
    "platform_fees",
    "payment_fees",
    "advertising_cost",
    "api_cost",
    "other_cost",
    "electricity_estimate",
)
DURATION_FIELDS = (
    "compute_seconds",
    "model_seconds",
    "browser_seconds",
    "human_minutes",
)
COUNT_FIELDS = (
    "leads",
    "visitors",
    "responses",
    "conversions",
    "customers",
    "repeat_customers",
)
def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("ledger timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("ledger timestamp must include a timezone")
    return parsed


def _bounded_identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is invalid")


def _nonnegative_decimal(name: str, value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a nonnegative finite Decimal or unknown")


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    business_model: str
    niche: str
    status: str
    gross_revenue: Decimal | None = None
    refunds: Decimal | None = None
    platform_fees: Decimal | None = None
    payment_fees: Decimal | None = None
    advertising_cost: Decimal | None = None
    api_cost: Decimal | None = None
    other_cost: Decimal | None = None
    electricity_estimate: Decimal | None = None
    compute_seconds: Decimal | None = None
    model_seconds: Decimal | None = None
    browser_seconds: Decimal | None = None
    human_minutes: Decimal | None = None
    leads: int | None = None
    visitors: int | None = None
    responses: int | None = None
    conversions: int | None = None
    customers: int | None = None
    repeat_customers: int | None = None
    created_at: str = ""
    updated_at: str = ""
    verdict: str | None = None

    def __post_init__(self) -> None:
        for name in ("experiment_id", "business_model", "niche", "status"):
            _bounded_identifier(name, getattr(self, name))
        for name in MONEY_FIELDS + DURATION_FIELDS:
            _nonnegative_decimal(name, getattr(self, name))
        for name in COUNT_FIELDS:
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a nonnegative integer or unknown")
        if (
            self.customers is not None
            and self.repeat_customers is not None
            and self.repeat_customers > self.customers
        ):
            raise ValueError("repeat_customers cannot exceed customers")
        created = parse_timestamp(self.created_at)
        updated = parse_timestamp(self.updated_at)
        if updated < created:
            raise ValueError("updated_at cannot precede created_at")
        if self.verdict is not None and (
            not isinstance(self.verdict, str) or not 1 <= len(self.verdict) <= 512
        ):
            raise ValueError("verdict must be bounded text or unknown")


EvidenceKind = Literal[
    "market_test_live",
    "legitimate_customer_payment",
    "monthly_revenue",
    "monthly_human_minutes",
    "monthly_recurring_revenue",
    "stable_unit_economics",
]
EVIDENCE_KINDS = {
    "market_test_live",
    "legitimate_customer_payment",
    "monthly_revenue",
    "monthly_human_minutes",
    "monthly_recurring_revenue",
    "stable_unit_economics",
}
RELATIONSHIPS = {"owner", "friend", "family", "stranger", "other_known"}


@dataclass(frozen=True)
class PromotionEvidence:
    evidence_id: str
    kind: EvidenceKind
    observed_at: str
    source_ref: str
    value: Decimal | None = None
    customer_relationship: str | None = None

    def __post_init__(self) -> None:
        _bounded_identifier("evidence_id", self.evidence_id)
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError("promotion evidence kind is not accepted")
        parse_timestamp(self.observed_at)
        if not isinstance(self.source_ref, str) or not 1 <= len(self.source_ref) <= 512:
            raise ValueError("promotion evidence source_ref is invalid")
        _nonnegative_decimal("promotion evidence value", self.value)
        if self.customer_relationship not in RELATIONSHIPS | {None}:
            raise ValueError("customer relationship is invalid")
        if self.kind == "legitimate_customer_payment" and (
            self.value is None
            or self.value <= 0
            or self.customer_relationship is None
        ):
            raise ValueError("customer payment requires positive value and relationship evidence")
