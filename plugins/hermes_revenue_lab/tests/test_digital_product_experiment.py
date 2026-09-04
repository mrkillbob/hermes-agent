from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from hermes_revenue_lab.experiments.digital_product import (
    ListingMetrics,
    NicheResearch,
    ProductSpecification,
    build_initial_portfolio,
    evaluate_sku_expansion,
)
from hermes_revenue_lab.scouts import ScoutCandidate, ScoutEvidence

OBSERVED = "2026-08-21T22:00:00+00:00"
DIGEST = "a" * 64


def candidate(
    index: int, *, generic_art: bool = False, weak: bool = False
) -> ScoutCandidate:
    candidate_id = f"digital-{index:03d}"
    product_type = "generic_ai_art" if generic_art else "calculator"
    evidence = [
        ScoutEvidence(
            evidence_id=f"{candidate_id}-type",
            source_url=f"https://market.example/{candidate_id}/type",
            source_class="public_page",
            permission_basis="publicly_accessible",
            collected_at=OBSERVED,
            content_sha256=DIGEST,
            fact_code="product_type",
            fact_value=product_type,
        ),
        ScoutEvidence(
            evidence_id=f"{candidate_id}-demand",
            source_url=f"https://market.example/{candidate_id}/demand",
            source_class="public_page",
            permission_basis="publicly_accessible",
            collected_at=OBSERVED,
            content_sha256="b" * 64,
            fact_code="demonstrable_demand",
            fact_value="Observed paid alternatives and buyer activity.",
        ),
    ]
    if not weak:
        evidence.extend(
            (
                ScoutEvidence(
                    evidence_id=f"{candidate_id}-buyer",
                    source_url=f"https://forum.example/{candidate_id}/buyer",
                    source_class="public_page",
                    permission_basis="publicly_accessible",
                    collected_at=OBSERVED,
                    content_sha256="c" * 64,
                    fact_code="buyer_language",
                    fact_value="Buyer describes the recurring workflow problem.",
                ),
                ScoutEvidence(
                    evidence_id=f"{candidate_id}-paid",
                    source_url=f"https://catalog.example/{candidate_id}/paid",
                    source_class="first_party_listing",
                    permission_basis="first_party_public",
                    collected_at=OBSERVED,
                    content_sha256="d" * 64,
                    fact_code="existing_paid_alternative",
                    fact_value="An existing paid functional alternative is listed.",
                ),
            )
        )
    return ScoutCandidate(
        candidate_id,
        "digital_product",
        f"Functional niche workflow {index}",
        tuple(evidence),
    )


def research() -> NicheResearch:
    return NicheResearch(
        tuple(candidate(index) for index in range(36)), observed_at=OBSERVED
    )


def specification(index: int) -> ProductSpecification:
    return ProductSpecification(
        product_id=f"product-{index:03d}",
        candidate_id=f"digital-{index:03d}",
        title=f"Private workflow calculator {index}",
        asset_type="calculator",
        functional_requirements=(
            "validated_inputs",
            "exact_calculation",
            "error_guidance",
        ),
    )


def metric(index: int, *, sales: int = 1) -> ListingMetrics:
    return ListingMetrics(
        product_id=f"product-{index:03d}",
        impressions=100,
        clicks=10,
        favorites=3,
        sales=sales,
        price=Decimal("19.00"),
        fees=Decimal("2.00"),
        refunds=0,
        observed_at=OBSERVED,
    )


class DigitalProductResearchTest(unittest.TestCase):
    def test_research_requires_at_least_three_dozen_unique_niches(self) -> None:
        observed = research()
        self.assertEqual(36, len(observed.candidates))
        with self.assertRaisesRegex(ValueError, "at least 36"):
            NicheResearch(
                tuple(candidate(index) for index in range(35)), observed_at=OBSERVED
            )
        with self.assertRaisesRegex(ValueError, "unique"):
            NicheResearch(
                tuple(candidate(index) for index in range(35)) + (candidate(0),),
                observed_at=OBSERVED,
            )

    def test_initial_portfolio_is_three_to_five_high_confidence_functional_assets(
        self,
    ) -> None:
        portfolio = build_initial_portfolio(
            research(), tuple(specification(index) for index in range(3))
        )
        self.assertEqual(3, len(portfolio.products))
        self.assertEqual("private_prototype", portfolio.status)
        self.assertIsNone(portfolio.marketplace)
        with self.assertRaisesRegex(ValueError, "three to five"):
            build_initial_portfolio(
                research(), tuple(specification(index) for index in range(2))
            )

        candidates = list(research().candidates)
        candidates[0] = candidate(0, weak=True)
        with self.assertRaisesRegex(ValueError, "high-confidence"):
            build_initial_portfolio(
                NicheResearch(tuple(candidates), observed_at=OBSERVED),
                tuple(specification(index) for index in range(3)),
            )

    def test_generic_art_and_nonfunctional_assets_are_rejected(self) -> None:
        candidates = list(research().candidates)
        candidates[0] = candidate(0, generic_art=True)
        with self.assertRaisesRegex(ValueError, "eligible digital-product"):
            NicheResearch(tuple(candidates), observed_at=OBSERVED)
        with self.assertRaisesRegex(ValueError, "functional asset"):
            replace(specification(0), asset_type="ai_art_bundle")


class DigitalProductDemandTest(unittest.TestCase):
    def test_metrics_preserve_unknowns_and_compute_exact_conversion(self) -> None:
        observed = metric(0)
        self.assertEqual(Decimal("0.01"), observed.conversion)
        unknown = replace(observed, impressions=None, sales=None, price=None, fees=None)
        self.assertIsNone(unknown.conversion)
        self.assertIsNone(unknown.net_revenue)

    def test_sku_expansion_requires_complete_real_demand_for_every_initial_product(
        self,
    ) -> None:
        portfolio = build_initial_portfolio(
            research(), tuple(specification(index) for index in range(3))
        )
        hold = evaluate_sku_expansion(
            portfolio, (metric(0), metric(1, sales=0), metric(2))
        )
        self.assertFalse(hold.eligible)
        self.assertIn("real_sales_evidence_missing", hold.reasons)

        unknown = evaluate_sku_expansion(
            portfolio,
            (metric(0), replace(metric(1), fees=None), metric(2)),
        )
        self.assertFalse(unknown.eligible)
        self.assertIn("complete_economics_missing", unknown.reasons)

        eligible = evaluate_sku_expansion(
            portfolio, tuple(metric(index) for index in range(3))
        )
        self.assertTrue(eligible.eligible)
        self.assertEqual((), eligible.reasons)

    def test_metrics_reject_impossible_funnel_or_refund_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "funnel"):
            replace(metric(0), clicks=101)
        with self.assertRaisesRegex(ValueError, "refunds"):
            replace(metric(0), refunds=2)


if __name__ == "__main__":
    unittest.main()
