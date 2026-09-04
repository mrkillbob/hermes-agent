from __future__ import annotations

import dataclasses
import unittest

from hermes_revenue_lab.models.corpus import CORPUS_VERSION, benchmark_corpus, corpus_digest


class ModelCorpusTest(unittest.TestCase):
    def test_exact_match_tasks_publish_deterministic_output_contracts(self) -> None:
        by_family = {task.family: task for task in benchmark_corpus()}

        expected_properties = {
            "analyze_business": {
                "business",
                "problem_codes",
                "evidence_ids",
                "competitor_evidence_ids",
            },
            "score_opportunity": {"demand", "automation", "policy_risk", "evidence_ids"},
            "synthesize_sources": {"conclusion_code", "source_ids"},
            "repair_collector": {"python", "function"},
            "structured_audit": {"finding_codes", "evidence_ids", "remedies"},
        }
        for family, property_names in expected_properties.items():
            schema = by_family[family].output_schema
            self.assertEqual(set(schema.get("properties", {})), property_names)
            self.assertFalse(schema.get("additionalProperties", True))

        self.assertIn("booking_validation", by_family["analyze_business"].prompt)
        self.assertIn("recurring_alert_candidate", by_family["synthesize_sources"].prompt)
        self.assertIn("high = 5", by_family["score_opportunity"].prompt)
        self.assertIn("Do not use try/except", by_family["repair_collector"].prompt)

    def test_fast_decision_prompts_define_the_exact_local_codebooks(self) -> None:
        """Catches exact validators requiring labels the model was never given."""
        by_family = {task.family: task for task in benchmark_corpus()}

        classification_prompt = by_family["classify_opportunities"].prompt
        escalation_prompt = by_family["decide_escalation"].prompt

        self.assertIn("reject = no cited observation", classification_prompt)
        self.assertIn("policy_conflict", escalation_prompt)
        self.assertIn("publication_action", escalation_prompt)
        self.assertIn("Include every applicable reason code", escalation_prompt)
        self.assertIn("Set escalate to true whenever any reason code applies", escalation_prompt)
        self.assertIn('contains "publish", include publication_action', escalation_prompt)

    def test_fast_structured_schemas_name_every_validator_field(self) -> None:
        """Catches generic schemas permitting semantically right data under the wrong field names."""
        by_family = {task.family: task for task in benchmark_corpus()}

        classification_items = by_family["classify_opportunities"].output_schema[
            "properties"
        ]["classifications"].get("items", {})
        extraction_items = by_family["extract_pages"].output_schema["properties"]["rows"].get(
            "items", {}
        )
        escalation_properties = by_family["decide_escalation"].output_schema.get(
            "properties", {}
        )

        self.assertEqual(["id", "category"], classification_items.get("required"))
        self.assertEqual(
            ["page_id", "vendor", "monthly_price", "updated"],
            extraction_items.get("required"),
        )
        self.assertEqual({"escalate", "reason_codes"}, set(escalation_properties))

    def test_corpus_has_all_required_task_families_and_fixture_counts(self) -> None:
        tasks = benchmark_corpus()
        self.assertEqual(len(tasks), 10)
        self.assertEqual(
            {task.family for task in tasks},
            {
                "classify_opportunities",
                "extract_pages",
                "deduplicate_records",
                "analyze_business",
                "score_opportunity",
                "select_tool",
                "synthesize_sources",
                "repair_collector",
                "structured_audit",
                "decide_escalation",
            },
        )
        by_family = {task.family: task for task in tasks}
        self.assertEqual(by_family["classify_opportunities"].fixture_count, 20)
        self.assertEqual(by_family["extract_pages"].fixture_count, 10)
        self.assertEqual(by_family["deduplicate_records"].fixture_count, 100)

    def test_exact_deduplication_is_marked_no_llm_for_production(self) -> None:
        task = next(
            task for task in benchmark_corpus() if task.family == "deduplicate_records"
        )
        self.assertEqual(task.production_tier, "no_llm")
        self.assertEqual(task.benchmark_tier, "fast")

    def test_fast_tasks_disable_thinking(self) -> None:
        for task in benchmark_corpus():
            if task.benchmark_tier == "fast":
                self.assertFalse(task.thinking_allowed, task.task_id)

    def test_corpus_is_immutable_local_and_stably_digestible(self) -> None:
        tasks = benchmark_corpus()
        self.assertTrue(CORPUS_VERSION.startswith("hrl.benchmark."))
        self.assertEqual(corpus_digest(tasks), corpus_digest(benchmark_corpus()))
        self.assertEqual(len(corpus_digest(tasks)), 64)
        for task in tasks:
            self.assertTrue(dataclasses.is_dataclass(task))
            if task.tool_schema_json is not None:
                self.assertIn("Emit", task.prompt)
            else:
                self.assertIn("Return", task.prompt)
            lowered = task.prompt.lower()
            self.assertNotIn("/users/", lowered)
            self.assertNotIn("tradingbot", lowered)
            self.assertNotIn("api_key", lowered)
            self.assertNotIn("password", lowered)

    def test_task_ids_are_unique_and_expected_answers_are_present(self) -> None:
        tasks = benchmark_corpus()
        self.assertEqual(len({task.task_id for task in tasks}), len(tasks))
        self.assertTrue(all(task.expected for task in tasks))


if __name__ == "__main__":
    unittest.main()
