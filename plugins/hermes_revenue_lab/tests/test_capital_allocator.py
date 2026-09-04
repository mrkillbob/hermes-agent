from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from hermes_revenue_lab.capital import CapitalEvidence, recommend_capital_action


def eligible() -> CapitalEvidence:
    return CapitalEvidence(
        experiment_id="experiment-001",
        contribution_margin=Decimal("125.50"),
        real_customer_count=4,
        automation_success_ratio=Decimal("0.92"),
        minimum_stable_windows=3,
        observed_stable_windows=3,
        compliance_green=True,
        human_minutes_per_fulfillment=Decimal(8),
        acceptable_human_minutes=Decimal(15),
        customer_evidence_refs=("ledger:sale-001", "ledger:sale-002"),
        source_refs=("artifact:run-001/verdict.json", "compliance:receipt-001"),
    )


class CapitalAllocatorTest(unittest.TestCase):
    def test_all_required_evidence_only_recommends_and_never_spends(self) -> None:
        result = recommend_capital_action(eligible())
        self.assertEqual("recommend_increase", result.action)
        self.assertTrue(result.requires_human_approval)
        self.assertFalse(result.actual_spend_allowed)
        self.assertEqual((), result.blocking_reasons)

    def test_missing_real_customer_evidence_holds(self) -> None:
        result = recommend_capital_action(
            replace(eligible(), real_customer_count=0, customer_evidence_refs=())
        )
        self.assertEqual("hold", result.action)
        self.assertIn("real_customer_evidence_missing", result.blocking_reasons)

    def test_negative_margin_or_red_compliance_kills(self) -> None:
        margin = recommend_capital_action(
            replace(eligible(), contribution_margin=Decimal("-0.01"))
        )
        compliance = recommend_capital_action(
            replace(eligible(), compliance_green=False)
        )
        self.assertEqual("kill", margin.action)
        self.assertEqual("kill", compliance.action)
        self.assertFalse(margin.actual_spend_allowed)
        self.assertFalse(compliance.actual_spend_allowed)

    def test_unstable_automation_or_excess_human_burden_modifies(self) -> None:
        unstable = recommend_capital_action(
            replace(eligible(), observed_stable_windows=2)
        )
        burden = recommend_capital_action(
            replace(eligible(), human_minutes_per_fulfillment=Decimal("15.01"))
        )
        self.assertEqual("modify", unstable.action)
        self.assertEqual("modify", burden.action)

    def test_unknown_evidence_fails_closed_to_hold(self) -> None:
        result = recommend_capital_action(
            replace(
                eligible(),
                contribution_margin=None,
                automation_success_ratio=None,
                compliance_green=None,
            )
        )
        self.assertEqual("hold", result.action)
        self.assertIn("contribution_margin_unknown", result.blocking_reasons)
        self.assertIn("automation_unknown", result.blocking_reasons)
        self.assertIn("compliance_unknown", result.blocking_reasons)

    def test_evidence_values_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "ratio"):
            replace(eligible(), automation_success_ratio=Decimal("1.01"))
        with self.assertRaisesRegex(ValueError, "customer evidence"):
            replace(eligible(), real_customer_count=1, customer_evidence_refs=())


if __name__ == "__main__":
    unittest.main()
