"""Conservative local-compute and business cost accounting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

REQUIRED_COST_CATEGORIES = (
    "platform_fees",
    "hosting",
    "domains",
    "apis",
    "marketplace_fees",
    "payment_fees",
    "refunds",
    "advertising",
)
_SECRET = re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]")


def _bounded(name: str, value: object) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 500:
        raise ValueError(f"{name} must be a bounded string")
    if _SECRET.search(value):
        raise ValueError(f"{name} source must not contain secret-shaped text")


def _nonnegative_decimal(name: str, value: object, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(
            f"{name} must be a nonnegative Decimal"
            + (" or unknown" if optional else "")
        )


@dataclass(frozen=True)
class ComputeAssumptions:
    active_compute_seconds: Decimal
    low_watts: Decimal
    high_watts: Decimal
    electricity_usd_per_kwh: Decimal
    measurement_basis: str
    electricity_price_source: str

    def __post_init__(self) -> None:
        _nonnegative_decimal("active compute seconds", self.active_compute_seconds)
        _nonnegative_decimal("low watts", self.low_watts)
        _nonnegative_decimal("high watts", self.high_watts)
        _nonnegative_decimal("electricity price", self.electricity_usd_per_kwh)
        if self.low_watts <= 0 or self.high_watts < self.low_watts:
            raise ValueError("power range must be positive and ordered")
        _bounded("measurement basis", self.measurement_basis)
        _bounded("electricity price", self.electricity_price_source)


@dataclass(frozen=True)
class CostItem:
    category: str
    amount_usd: Decimal | None
    source_ref: str

    def __post_init__(self) -> None:
        if self.category not in REQUIRED_COST_CATEGORIES:
            raise ValueError("cost category is not recognized")
        _nonnegative_decimal("cost amount", self.amount_usd, optional=True)
        _bounded("cost source", self.source_ref)


@dataclass(frozen=True)
class CostEstimate:
    estimated_compute_cost_usd: Decimal
    estimated_compute_cost_low_usd: Decimal
    estimated_compute_cost_high_usd: Decimal
    compute_precision: Literal["estimate_interval"]
    compute_assumptions: ComputeAssumptions
    known_non_compute_cost_usd: Decimal
    unknown_cost_categories: tuple[str, ...]
    known_cost_lower_bound_usd: Decimal
    total_status: Literal["estimated_range", "unknown"]
    estimated_total_cost_low_usd: Decimal | None
    estimated_total_cost_high_usd: Decimal | None


def estimate_costs(
    compute: ComputeAssumptions,
    items: tuple[CostItem, ...],
) -> CostEstimate:
    """Estimate cost without claiming rough power data is exact."""

    categories = tuple(item.category for item in items)
    if len(categories) != len(REQUIRED_COST_CATEGORIES) or set(categories) != set(
        REQUIRED_COST_CATEGORIES
    ):
        raise ValueError("every required cost category must appear exactly once")

    compute_hours = compute.active_compute_seconds / Decimal(3600)
    low = (
        compute_hours
        * (compute.low_watts / Decimal(1000))
        * compute.electricity_usd_per_kwh
    )
    high = (
        compute_hours
        * (compute.high_watts / Decimal(1000))
        * compute.electricity_usd_per_kwh
    )
    midpoint = (low + high) / Decimal(2)
    known_non_compute = sum(
        (item.amount_usd for item in items if item.amount_usd is not None), Decimal(0)
    )
    unknown = tuple(
        category
        for category in REQUIRED_COST_CATEGORIES
        if next(item for item in items if item.category == category).amount_usd is None
    )
    total_status: Literal["estimated_range", "unknown"] = (
        "unknown" if unknown else "estimated_range"
    )
    return CostEstimate(
        estimated_compute_cost_usd=midpoint,
        estimated_compute_cost_low_usd=low,
        estimated_compute_cost_high_usd=high,
        compute_precision="estimate_interval",
        compute_assumptions=compute,
        known_non_compute_cost_usd=known_non_compute,
        unknown_cost_categories=unknown,
        known_cost_lower_bound_usd=known_non_compute + low,
        total_status=total_status,
        estimated_total_cost_low_usd=None if unknown else known_non_compute + low,
        estimated_total_cost_high_usd=None if unknown else known_non_compute + high,
    )
