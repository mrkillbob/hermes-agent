from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from hermes_revenue_lab.costs import (
    REQUIRED_COST_CATEGORIES,
    ComputeAssumptions,
    CostItem,
    estimate_costs,
)


def assumptions() -> ComputeAssumptions:
    return ComputeAssumptions(
        active_compute_seconds=Decimal(3600),
        low_watts=Decimal(50),
        high_watts=Decimal(100),
        electricity_usd_per_kwh=Decimal("0.20"),
        measurement_basis="bounded Mac power estimate",
        electricity_price_source="operator-config:2026-08",
    )


def items(*, unknown: str | None = None) -> tuple[CostItem, ...]:
    return tuple(
        CostItem(
            category=category,
            amount_usd=None if category == unknown else Decimal(1),
            source_ref=f"ledger:cost:{category}",
        )
        for category in REQUIRED_COST_CATEGORIES
    )


class CostAccountingTest(unittest.TestCase):
    def test_compute_cost_is_an_interval_with_retained_assumptions(self) -> None:
        result = estimate_costs(assumptions(), items())
        self.assertEqual(Decimal("0.010"), result.estimated_compute_cost_low_usd)
        self.assertEqual(Decimal("0.020"), result.estimated_compute_cost_high_usd)
        self.assertEqual(Decimal("0.015"), result.estimated_compute_cost_usd)
        self.assertEqual("estimate_interval", result.compute_precision)
        self.assertEqual(assumptions(), result.compute_assumptions)

    def test_all_required_categories_are_accounted_for(self) -> None:
        result = estimate_costs(assumptions(), items())
        self.assertEqual(Decimal(8), result.known_non_compute_cost_usd)
        self.assertEqual((), result.unknown_cost_categories)
        self.assertEqual("estimated_range", result.total_status)
        self.assertEqual(Decimal("8.010"), result.estimated_total_cost_low_usd)
        self.assertEqual(Decimal("8.020"), result.estimated_total_cost_high_usd)

    def test_unknown_category_keeps_total_unknown_not_zero(self) -> None:
        result = estimate_costs(assumptions(), items(unknown="advertising"))
        self.assertEqual(("advertising",), result.unknown_cost_categories)
        self.assertEqual("unknown", result.total_status)
        self.assertIsNone(result.estimated_total_cost_low_usd)
        self.assertIsNone(result.estimated_total_cost_high_usd)
        self.assertEqual(Decimal("7.010"), result.known_cost_lower_bound_usd)

    def test_missing_duplicate_or_unknown_categories_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly once"):
            estimate_costs(assumptions(), items()[:-1])
        with self.assertRaisesRegex(ValueError, "exactly once"):
            estimate_costs(assumptions(), items() + (items()[0],))
        with self.assertRaisesRegex(ValueError, "category"):
            CostItem("other", Decimal(1), "ledger:other")

    def test_invalid_power_range_and_secret_shaped_sources_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "power range"):
            replace(assumptions(), low_watts=Decimal(101))
        with self.assertRaisesRegex(ValueError, "source"):
            replace(assumptions(), electricity_price_source="api_key=super-secret")


if __name__ == "__main__":
    unittest.main()
