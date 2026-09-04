from __future__ import annotations

import json
import unittest

from hermes_revenue_lab.models.corpus import benchmark_corpus
from hermes_revenue_lab.models.validators import evaluate_response


def task(family: str):
    return next(item for item in benchmark_corpus() if item.family == family)


class ModelValidatorTest(unittest.TestCase):
    def test_exact_classification_passes(self) -> None:
        item = task("classify_opportunities")
        evaluation = evaluate_response(item, item.expected_json)
        self.assertTrue(evaluation.structured_valid)
        self.assertEqual(evaluation.correctness, 1.0)
        self.assertTrue(evaluation.success)

    def test_malformed_or_hallucinated_output_fails(self) -> None:
        item = task("classify_opportunities")
        malformed = evaluate_response(item, "```json\n{}\n```")
        self.assertFalse(malformed.structured_valid)
        self.assertIn("invalid_json", malformed.reason_codes)

        payload = json.loads(item.expected_json)
        payload["classifications"][0]["category"] = "invented"
        wrong = evaluate_response(item, json.dumps(payload))
        self.assertEqual(wrong.correctness, 0.0)
        self.assertFalse(wrong.success)

    def test_thinking_channel_is_rejected_when_disabled(self) -> None:
        item = task("decide_escalation")
        response = f"<think>long hidden reasoning</think>\n{item.expected_json}"
        evaluation = evaluate_response(item, response)
        self.assertTrue(evaluation.unnecessary_thinking)
        self.assertFalse(evaluation.success)
        self.assertIn("unexpected_thinking", evaluation.reason_codes)

    def test_tool_selection_requires_exact_declared_tool_and_arguments(self) -> None:
        item = task("select_tool")
        correct = evaluate_response(
            item,
            "{}",
            tool_call={
                "name": "store_candidate",
                "arguments": {"candidate_id": "C-17", "score": 4},
            },
        )
        self.assertTrue(correct.tool_call_correct)
        self.assertTrue(correct.success)

        wrong = evaluate_response(
            item,
            "{}",
            tool_call={"name": "delete_candidate", "arguments": {"candidate_id": "C-17"}},
        )
        self.assertFalse(wrong.tool_call_correct)
        self.assertFalse(wrong.success)

    def test_coding_validator_accepts_bounded_collector_and_rejects_imports(self) -> None:
        item = task("repair_collector")
        safe = {
            "function": "parse_prices",
            "python": (
                "def parse_prices(text):\n"
                "    result = []\n"
                "    for line in text.splitlines():\n"
                "        parts = line.split(',')\n"
                "        if len(parts) != 2 or not parts[1].isdigit():\n"
                "            continue\n"
                "        result.append(int(parts[1]))\n"
                "    return result\n"
            ),
        }
        accepted = evaluate_response(item, json.dumps(safe))
        self.assertTrue(accepted.success)

        unsafe = {"function": "parse_prices", "python": "import os\ndef parse_prices(text): return []"}
        rejected = evaluate_response(item, json.dumps(unsafe))
        self.assertFalse(rejected.success)
        self.assertIn("unsafe_python", rejected.reason_codes)

    def test_audit_cannot_add_unsupported_findings(self) -> None:
        item = task("structured_audit")
        supported = {
            "finding_codes": ["broken_link", "missing_alt_text"],
            "evidence_ids": ["A1", "A2"],
            "remedies": ["repair the cited link", "add descriptive alt text"],
        }
        self.assertTrue(evaluate_response(item, json.dumps(supported)).success)
        supported["finding_codes"].append("slow_site")
        evaluation = evaluate_response(item, json.dumps(supported))
        self.assertFalse(evaluation.success)
        self.assertIn("incorrect_output", evaluation.reason_codes)


if __name__ == "__main__":
    unittest.main()
