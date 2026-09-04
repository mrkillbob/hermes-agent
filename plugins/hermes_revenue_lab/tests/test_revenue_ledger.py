from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from hermes_revenue_lab.ledger import (
    ExperimentRecord,
    PromotionEvidence,
    RevenueLedger,
    derive_metrics,
    highest_promotion_stage,
)


CREATED = "2026-08-01T00:00:00+00:00"
UPDATED = "2026-08-11T00:00:00+00:00"


def record(**overrides: object) -> ExperimentRecord:
    values = {
        "experiment_id": "audit-shop-001",
        "business_model": "productized_audit",
        "niche": "local_retail",
        "status": "research",
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    values.update(overrides)
    return ExperimentRecord(**values)


def evidence(
    evidence_id: str,
    kind: str,
    *,
    value: str | None = None,
    relationship: str | None = None,
) -> PromotionEvidence:
    return PromotionEvidence(
        evidence_id=evidence_id,
        kind=kind,
        observed_at=UPDATED,
        source_ref=f"local:{evidence_id}",
        value=None if value is None else Decimal(value),
        customer_relationship=relationship,
    )


class RevenueMetricsTest(unittest.TestCase):
    def test_exact_metrics_use_all_required_known_inputs(self) -> None:
        observed = record(
            gross_revenue=Decimal("200"),
            refunds=Decimal("10"),
            platform_fees=Decimal("10"),
            payment_fees=Decimal("5"),
            advertising_cost=Decimal("20"),
            api_cost=Decimal("5"),
            other_cost=Decimal("10"),
            electricity_estimate=Decimal("5"),
            compute_seconds=Decimal("7200"),
            model_seconds=Decimal("300"),
            browser_seconds=Decimal("120"),
            human_minutes=Decimal("120"),
            leads=20,
            visitors=100,
            responses=10,
            conversions=4,
            customers=4,
            repeat_customers=1,
            updated_at=UPDATED,
        )

        metrics = derive_metrics(observed)

        self.assertEqual(Decimal("175"), metrics.net_revenue)
        self.assertEqual(Decimal("135"), metrics.contribution_profit)
        self.assertEqual(Decimal("5"), metrics.cac)
        self.assertEqual(Decimal("0.04"), metrics.conversion_rate)
        self.assertEqual(Decimal("8.75"), metrics.revenue_per_lead)
        self.assertEqual(Decimal("43.75"), metrics.revenue_per_customer)
        self.assertEqual(Decimal("67.5"), metrics.roch)
        self.assertEqual(Decimal("67.5"), metrics.rohh)
        self.assertEqual(Decimal("13.5"), metrics.profit_per_day)
        self.assertEqual(Decimal("410.90625"), metrics.profit_per_month)

    def test_unknowns_and_zero_denominators_remain_unknown(self) -> None:
        unknown = derive_metrics(record(gross_revenue=Decimal("100"), refunds=Decimal("0")))
        zero = derive_metrics(
            record(
                gross_revenue=Decimal("0"),
                refunds=Decimal("0"),
                platform_fees=Decimal("0"),
                payment_fees=Decimal("0"),
                advertising_cost=Decimal("0"),
                api_cost=Decimal("0"),
                other_cost=Decimal("0"),
                electricity_estimate=Decimal("0"),
                compute_seconds=Decimal("0"),
                human_minutes=Decimal("0"),
                leads=0,
                visitors=0,
                customers=0,
            )
        )
        self.assertIsNone(unknown.net_revenue)
        self.assertIsNone(unknown.contribution_profit)
        self.assertIsNone(zero.contribution_margin)
        self.assertIsNone(zero.cac)
        self.assertIsNone(zero.conversion_rate)
        self.assertIsNone(zero.roch)
        self.assertIsNone(zero.rohh)


class RevenuePromotionTest(unittest.TestCase):
    def test_promotion_is_sequential_and_bound_to_raw_evidence(self) -> None:
        profitable = record(
            gross_revenue=Decimal("120"),
            refunds=Decimal("0"),
            platform_fees=Decimal("5"),
            payment_fees=Decimal("5"),
            advertising_cost=Decimal("10"),
            api_cost=Decimal("5"),
            other_cost=Decimal("0"),
            electricity_estimate=Decimal("0"),
        )
        market = evidence("market", "market_test_live")
        owner = evidence(
            "owner-sale", "legitimate_customer_payment", value="10", relationship="owner"
        )
        stranger = evidence(
            "stranger-sale", "legitimate_customer_payment", value="10", relationship="stranger"
        )
        self.assertEqual("E0", highest_promotion_stage(profitable, ()))
        self.assertEqual("E1", highest_promotion_stage(profitable, (market, owner)))
        self.assertEqual("E4", highest_promotion_stage(profitable, (market, stranger)))

        advanced = (
            market,
            stranger,
            evidence("monthly", "monthly_revenue", value="250"),
            evidence("labor", "monthly_human_minutes", value="119.9"),
            evidence("mrr", "monthly_recurring_revenue", value="500"),
            evidence("economics", "stable_unit_economics", value="1"),
        )
        self.assertEqual("E7", highest_promotion_stage(profitable, advanced))

    def test_ai_opinion_is_not_promotion_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence kind"):
            evidence("idea", "ai_opinion", value="1")


class RevenueLedgerStoreTest(unittest.TestCase):
    def test_round_trip_preserves_nulls_decimals_and_audit_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = RevenueLedger(root / "private" / "revenue.sqlite3", allowed_root=root)
            initial = record()
            ledger.create_experiment(initial)
            self.assertEqual(0o600, ledger.database.stat().st_mode & 0o777)

            stored = ledger.get_experiment(initial.experiment_id)
            self.assertEqual(initial, stored)
            connection = sqlite3.connect(ledger.database)
            raw = connection.execute(
                "select gross_revenue, customers from experiments where experiment_id = ?",
                (initial.experiment_id,),
            ).fetchone()
            connection.close()
            self.assertEqual((None, None), raw)

            updated = replace(
                initial,
                gross_revenue=Decimal("12.34"),
                customers=1,
                updated_at=UPDATED,
            )
            ledger.update_experiment(updated, expected_revision=1)
            self.assertEqual(updated, ledger.get_experiment(initial.experiment_id))
            self.assertEqual(("experiment_created", "experiment_updated"), ledger.event_types())
            with self.assertRaisesRegex(ValueError, "revision conflict"):
                ledger.update_experiment(updated, expected_revision=1)

    def test_raw_evidence_and_archive_findings_are_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = RevenueLedger(root / "revenue.sqlite3", allowed_root=root)
            initial = record()
            ledger.create_experiment(initial)
            observed = evidence(
                "stranger-sale",
                "legitimate_customer_payment",
                value="9.99",
                relationship="stranger",
            )
            ledger.add_promotion_evidence(initial.experiment_id, observed)
            self.assertEqual((observed,), ledger.list_promotion_evidence(initial.experiment_id))

            ledger.archive_experiment(
                initial.experiment_id,
                reason_codes=("no_meaningful_engagement",),
                findings="Sufficient exposure produced no qualified responses.",
                observed_at=UPDATED,
                expected_revision=1,
            )
            archived = ledger.get_experiment(initial.experiment_id)
            self.assertEqual("archived", archived.status)
            self.assertEqual("killed:no_meaningful_engagement", archived.verdict)
            self.assertEqual(
                ("no_meaningful_engagement",),
                ledger.get_archive_findings(initial.experiment_id)["reason_codes"],
            )

    def test_database_must_be_root_contained_and_not_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "outside"):
                RevenueLedger(root.parent / "outside.sqlite3", allowed_root=root)
            target = root / "target.sqlite3"
            target.touch()
            link = root / "ledger.sqlite3"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                RevenueLedger(link, allowed_root=root)


if __name__ == "__main__":
    unittest.main()
