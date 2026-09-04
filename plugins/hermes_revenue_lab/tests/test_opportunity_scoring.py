from __future__ import annotations

import unittest

from hermes_revenue_lab.opportunities import (
    DIMENSIONS,
    OPPORTUNITY_FIELDS,
    RANKING_FACTORS,
    DimensionScore,
    EvidenceItem,
    FactorRating,
    ObservedField,
    OpportunityCandidate,
    build_assessment,
    rank_assessments,
)


OBSERVED = "2026-08-21T00:00:00+00:00"


def candidate() -> OpportunityCandidate:
    evidence = tuple(
        EvidenceItem(
            evidence_id=f"ev-{field_name}",
            field_name=field_name,
            statement=f"Observed evidence for {field_name}",
            source_ref=f"local:{field_name}",
            observed_at=OBSERVED,
        )
        for field_name in OPPORTUNITY_FIELDS
    )
    observations = tuple(
        ObservedField(
            field_name=field_name,
            status="observed",
            value=f"observed:{field_name}",
            evidence_ids=(f"ev-{field_name}",),
        )
        for field_name in OPPORTUNITY_FIELDS
    )
    return OpportunityCandidate("local-audit-001", observations, evidence)


SCORE_EVIDENCE = {
    "demand": "ev-urgency",
    "monetizability": "ev-willingness_to_pay",
    "automation": "ev-automation_percentage",
    "competition": "ev-competition",
    "defensibility": "ev-moat",
    "cost": "ev-startup_cost",
    "risk": "ev-policy_risk",
    "time_to_revenue": "ev-time_to_first_revenue",
}
FACTOR_EVIDENCE = {
    "expected_value": "ev-willingness_to_pay",
    "automation": "ev-automation_percentage",
    "recurrence": "ev-recurring_revenue_potential",
    "defensibility": "ev-moat",
    "human_labor": "ev-human_effort",
    "capital_required": "ev-startup_cost",
    "platform_risk": "ev-platform_dependency",
}


def scores(band: str = "high") -> tuple[DimensionScore, ...]:
    return tuple(
        DimensionScore(name, band, (SCORE_EVIDENCE[name],), f"{name}_evidence")
        for name in DIMENSIONS
    )


def factors(
    upside: str = "high",
    burden: str = "low",
) -> tuple[FactorRating, ...]:
    return tuple(
        FactorRating(
            name,
            burden if name in {"human_labor", "capital_required", "platform_risk"} else upside,
            (FACTOR_EVIDENCE[name],),
        )
        for name in RANKING_FACTORS
    )


class OpportunitySchemaTest(unittest.TestCase):
    def test_schema_contains_every_required_field_and_raw_evidence(self) -> None:
        observed = candidate()
        self.assertEqual(set(OPPORTUNITY_FIELDS), {field.field_name for field in observed.fields})
        self.assertEqual(len(OPPORTUNITY_FIELDS), len(observed.evidence))
        self.assertTrue(all(field.evidence_ids for field in observed.fields))

    def test_missing_field_or_evidence_fails_closed(self) -> None:
        observed = candidate()
        with self.assertRaisesRegex(ValueError, "complete opportunity schema"):
            OpportunityCandidate(
                observed.opportunity_id,
                observed.fields[:-1],
                observed.evidence,
            )
        with self.assertRaisesRegex(ValueError, "evidence reference"):
            OpportunityCandidate(
                observed.opportunity_id,
                observed.fields,
                observed.evidence[:-1],
            )

    def test_unavailable_field_remains_none_with_evidence(self) -> None:
        unavailable = ObservedField(
            "market_size_proxy",
            "unavailable",
            None,
            ("ev-market_size_proxy",),
        )
        self.assertIsNone(unavailable.value)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            ObservedField("market_size_proxy", "unavailable", "medium", ("ev-x",))


class OpportunityRankingTest(unittest.TestCase):
    def test_assessment_requires_exact_ordinal_dimensions_and_relevant_evidence(self) -> None:
        observed = candidate()
        assessment = build_assessment(observed, scores(), factors())
        self.assertEqual("A", assessment.ranking_tier)
        self.assertEqual(set(DIMENSIONS), {score.dimension for score in assessment.scores})
        with self.assertRaisesRegex(ValueError, "ordinal band"):
            DimensionScore("demand", "4.7", ("ev-urgency",), "fake_precision")
        invalid = list(scores())
        invalid[0] = DimensionScore(
            "demand", "high", ("ev-policy_risk",), "cross_domain_evidence"
        )
        with self.assertRaisesRegex(ValueError, "not relevant"):
            build_assessment(observed, tuple(invalid), factors())

    def test_primary_formula_orders_upside_over_burden_without_public_fake_precision(self) -> None:
        observed = candidate()
        strong = build_assessment(observed, scores("high"), factors("very_high", "very_low"))
        weak_candidate = OpportunityCandidate(
            "local-audit-002", observed.fields, observed.evidence
        )
        weak = build_assessment(
            weak_candidate,
            scores("low"),
            factors("low", "very_high"),
        )
        self.assertEqual(("local-audit-001", "local-audit-002"), rank_assessments((weak, strong)))
        self.assertEqual("A", strong.ranking_tier)
        self.assertEqual("E", weak.ranking_tier)
        self.assertFalse(hasattr(strong, "ranking_score"))


if __name__ == "__main__":
    unittest.main()
