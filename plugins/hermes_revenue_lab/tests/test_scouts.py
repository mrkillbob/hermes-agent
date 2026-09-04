from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes_revenue_lab.scouts import (
    ScoutCandidate,
    ScoutEvidence,
    ScoutStore,
    evaluate_candidate,
)


OBSERVED = "2026-08-21T00:00:00+00:00"
DIGEST = "a" * 64


def fact(
    evidence_id: str,
    code: str,
    value: str = "observed",
    *,
    source_class: str = "public_page",
) -> ScoutEvidence:
    return ScoutEvidence(
        evidence_id=evidence_id,
        source_url=f"https://public.example/{evidence_id}",
        source_class=source_class,
        permission_basis="publicly_accessible",
        collected_at=OBSERVED,
        content_sha256=DIGEST,
        fact_code=code,
        fact_value=value,
    )


class ScoutEligibilityTest(unittest.TestCase):
    def test_business_problem_requires_objective_fact_not_llm_opinion(self) -> None:
        inferred = ScoutCandidate(
            "business-001",
            "business_problem",
            "Example business",
            (fact("opinion", "llm_opinion", "website probably poor"),),
        )
        measured = ScoutCandidate(
            "business-002",
            "business_problem",
            "Measured business",
            (fact("broken", "broken_conversion_path", "checkout returns HTTP 500"),),
        )
        self.assertFalse(evaluate_candidate(inferred).eligible)
        self.assertEqual(
            ("objective_problem_evidence_missing",),
            evaluate_candidate(inferred).reasons,
        )
        self.assertTrue(evaluate_candidate(measured).eligible)

    def test_data_opportunity_requires_history_moat_and_all_economic_facts(self) -> None:
        evidence = (
            fact("fragmented", "fragmented_sources"),
            fact("updates", "repeated_updates"),
            fact("economic", "economically_useful"),
            fact("history", "historical_dataset_value"),
        )
        complete = ScoutCandidate("data-001", "data_opportunity", "Permit history", evidence)
        incomplete = ScoutCandidate(
            "data-002", "data_opportunity", "Static directory", evidence[:-1]
        )
        self.assertTrue(evaluate_candidate(complete).eligible)
        self.assertFalse(evaluate_candidate(incomplete).eligible)
        self.assertIn("historical_value_missing", evaluate_candidate(incomplete).reasons)

    def test_alert_requires_authoritative_source_and_monetary_time_value(self) -> None:
        facts = (
            fact("event", "public_rfp", source_class="authoritative_public"),
            fact("value", "notification_monetary_value", "early bid window"),
        )
        eligible = ScoutCandidate("alert-001", "alert_opportunity", "Public RFP", facts)
        weak = ScoutCandidate(
            "alert-002",
            "alert_opportunity",
            "Rumored RFP",
            tuple(
                ScoutEvidence(
                    item.evidence_id,
                    item.source_url,
                    "public_page",
                    item.permission_basis,
                    item.collected_at,
                    item.content_sha256,
                    item.fact_code,
                    item.fact_value,
                )
                for item in facts
            ),
        )
        self.assertTrue(evaluate_candidate(eligible).eligible)
        self.assertIn("authoritative_source_missing", evaluate_candidate(weak).reasons)

    def test_digital_product_requires_demand_and_rejects_generic_ai_art(self) -> None:
        useful = ScoutCandidate(
            "digital-001",
            "digital_product",
            "Niche calculator",
            (fact("type", "product_type", "calculator"), fact("demand", "demonstrable_demand")),
        )
        spam = ScoutCandidate(
            "digital-002",
            "digital_product",
            "Prompt art bundle",
            (
                fact("spam-type", "product_type", "generic_ai_art"),
                fact("spam-demand", "demonstrable_demand"),
            ),
        )
        self.assertTrue(evaluate_candidate(useful).eligible)
        self.assertEqual(("generic_ai_art_rejected",), evaluate_candidate(spam).reasons)


class ScoutStoreTest(unittest.TestCase):
    def test_store_preserves_raw_evidence_and_rejected_verdict(self) -> None:
        candidate = ScoutCandidate(
            "business-001",
            "business_problem",
            "Example business",
            (fact("opinion", "llm_opinion", "website probably poor"),),
        )
        verdict = evaluate_candidate(candidate)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ScoutStore(root / "scouts.sqlite3", allowed_root=root)
            store.record(candidate, verdict)
            loaded, loaded_verdict = store.load("business-001")
            self.assertEqual(candidate, loaded)
            self.assertEqual(verdict, loaded_verdict)
            self.assertEqual(0o600, store.database.stat().st_mode & 0o777)
            with self.assertRaisesRegex(ValueError, "already recorded"):
                store.record(candidate, verdict)

    def test_candidate_evidence_and_run_are_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence bound"):
            ScoutCandidate(
                "data-too-large",
                "data_opportunity",
                "Unbounded",
                tuple(fact(f"fact-{index}", "fragmented_sources") for index in range(65)),
            )


if __name__ == "__main__":
    unittest.main()
