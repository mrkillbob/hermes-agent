from __future__ import annotations

import unittest
from pathlib import Path

from hermes_revenue_lab.experiments import (
    SELECTED_VERTICAL,
    AuditFinding,
    BusinessTarget,
    build_experiment_a,
    render_sample_audit,
)
from hermes_revenue_lab.scouts import ScoutCandidate, ScoutEvidence


def target(index: int) -> BusinessTarget:
    evidence_id = f"broken-{index:03d}"
    candidate = ScoutCandidate(
        f"candidate-{index:03d}",
        "business_problem",
        f"HVAC business {index:03d}",
        (
            ScoutEvidence(
                evidence_id,
                f"https://public.example/hvac/{index:03d}",
                "first_party_listing",
                "first_party_public",
                "2026-08-21T00:00:00+00:00",
                f"{index:064x}"[-64:],
                "broken_conversion_path",
                "quote form returns an HTTP error",
            ),
        ),
    )
    return BusinessTarget(f"target-{index:03d}", SELECTED_VERTICAL.vertical_id, candidate)


def finding(index: int) -> AuditFinding:
    return AuditFinding(
        finding_id=f"finding-{index:03d}",
        target_id=f"target-{index:03d}",
        problem="Public quote conversion path fails",
        evidence_ids=(f"broken-{index:03d}",),
        consequence="A prospective customer cannot submit a quote request.",
        remedy="Repair the form endpoint and verify a successful test submission.",
        confidence="high",
        competitor_comparison="A nearby competitor exposes a working quote form.",
    )


class ExperimentATest(unittest.TestCase):
    def test_one_vertical_100_businesses_and_10_to_20_findings(self) -> None:
        batch = build_experiment_a(
            tuple(target(index) for index in range(100)),
            tuple(finding(index) for index in range(12)),
        )
        self.assertEqual("independent_hvac_sacramento_ca", batch.vertical.vertical_id)
        self.assertEqual(100, len(batch.targets))
        self.assertEqual(12, len(batch.findings))
        self.assertTrue(all(price.status == "hypothesis" for price in batch.price_hypotheses))

    def test_cohort_and_confidence_bounds_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "100-business"):
            build_experiment_a(
                tuple(target(index) for index in range(20)),
                tuple(finding(index) for index in range(12)),
            )
        with self.assertRaisesRegex(ValueError, "10 to 20"):
            build_experiment_a(
                tuple(target(index) for index in range(100)),
                tuple(finding(index) for index in range(21)),
            )

    def test_sample_audit_exposes_required_evidence_and_is_not_outreach(self) -> None:
        sample = render_sample_audit(finding(1))
        self.assertEqual("Public quote conversion path fails", sample["problem"])
        self.assertEqual(["broken-001"], sample["evidence_ids"])
        self.assertIn("practical_remedy", sample)
        self.assertEqual("sample_not_customer_contact", sample["status"])

    def test_experiment_a_source_has_no_outreach_or_impersonation_transport(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "hermes_revenue_lab"
            / "experiments"
            / "b2b_opportunity.py"
        ).read_text()
        self.assertNotIn("send_email", source)
        self.assertNotIn("mass_outreach", source)
        self.assertNotIn("impersonat", source)


if __name__ == "__main__":
    unittest.main()
