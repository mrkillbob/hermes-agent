from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_revenue_lab.routing.policy import load_verified_policy
from hermes_revenue_lab.routing.router import (
    ModelExecutionError,
    ModelRouter,
    TierUnavailableError,
)
from hermes_revenue_lab.routing.types import TaskExecutionReceipt


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "model_benchmarks"


def verified_policy():
    return load_verified_policy(
        ROOT / "config" / "model_routing_policy.json",
        ARTIFACT_ROOT / "model_benchmark.json",
        ARTIFACT_ROOT / "model_selections.json",
        ARTIFACT_ROOT / "model_benchmark_checksums.sha256",
    )


def receipt(decision, value=None):
    return TaskExecutionReceipt(
        value=value,
        actual_model=decision.actual_model,
        model_digest=decision.model_digest,
    )


class ModelRouterTest(unittest.TestCase):
    def test_fast_resolves_only_to_verified_hrl1_winner(self) -> None:
        decision = ModelRouter(verified_policy()).resolve("fast", "classify.batch-1")

        self.assertEqual("fast", decision.requested_tier)
        self.assertEqual("qwen3.5:4b", decision.actual_model)
        self.assertEqual("2a654d98e6fb", decision.model_digest)
        self.assertFalse(decision.thinking)

    def test_unavailable_tier_records_event_without_calling_executor(self) -> None:
        called = False
        events = []

        def executor(_decision):
            nonlocal called
            called = True

        router = ModelRouter(verified_policy(), event_sink=events.append)
        with self.assertRaises(TierUnavailableError) as raised:
            router.execute("standard", "research.vendor-1", executor)

        self.assertFalse(called)
        self.assertEqual("unavailable", raised.exception.event.task_result)
        self.assertEqual(0, raised.exception.event.retries)
        self.assertEqual([raised.exception.event], events)

    def test_active_luna_blocks_model_but_allows_no_llm(self) -> None:
        called = []
        router = ModelRouter(verified_policy())

        with self.assertRaises(TierUnavailableError) as raised:
            router.execute(
                "fast",
                "classify.batch-1",
                lambda decision: called.append(decision),
                luna_active=True,
            )
        result, event = router.execute(
            "no_llm",
            "hash.document-1",
            lambda decision: (called.append(decision), receipt(decision, "hashed"))[1],
            luna_active=True,
        )

        self.assertEqual("blocked_luna", raised.exception.event.task_result)
        self.assertEqual("hashed", result)
        self.assertIsNone(called[0].actual_model)
        self.assertTrue(event.success)

    def test_escalation_requires_bounded_reason_code_before_availability_check(self) -> None:
        router = ModelRouter(verified_policy())
        with self.assertRaisesRegex(ValueError, "escalation reason"):
            router.resolve("escalation", "review.product-1")
        with self.assertRaisesRegex(ValueError, "reason code"):
            router.resolve(
                "escalation",
                "review.product-1",
                escalation_reason="contains raw customer text",
            )

    def test_retry_reuses_one_model_and_records_complete_metadata(self) -> None:
        decisions = []
        attempts = 0
        base = datetime(2026, 8, 21, tzinfo=timezone.utc)
        utc_values = iter((base, base + timedelta(seconds=3)))
        monotonic_values = iter((10.0, 13.25))

        def executor(decision):
            nonlocal attempts
            decisions.append(decision)
            attempts += 1
            if attempts == 1:
                raise RuntimeError("secret response body")
            return receipt(decision, {"private": "result is returned but never logged"})

        router = ModelRouter(
            verified_policy(),
            utc_now=lambda: next(utc_values),
            monotonic=lambda: next(monotonic_values),
            event_id_provider=lambda: "event-001",
        )
        result, event = router.execute(
            "fast",
            "classify.batch-1",
            executor,
            max_retries=1,
        )

        self.assertEqual(result["private"], "result is returned but never logged")
        self.assertEqual(["qwen3.5:4b", "qwen3.5:4b"], [item.actual_model for item in decisions])
        self.assertEqual("event-001", event.event_id)
        self.assertEqual(1, event.retries)
        self.assertEqual(3.25, event.wall_time_seconds)
        self.assertEqual("succeeded", event.task_result)
        self.assertEqual(3.25, event.estimated_compute_cost["local_compute_seconds"])
        self.assertIsNone(event.estimated_compute_cost["monetary_cost"])
        self.assertNotIn("secret", str(event.canonical_record()))

    def test_exhausted_retry_records_categorical_failure_without_error_text(self) -> None:
        events = []
        router = ModelRouter(verified_policy(), event_sink=events.append)

        with self.assertRaises(ModelExecutionError) as raised:
            router.execute(
                "fast",
                "classify.batch-2",
                lambda _decision: (_ for _ in ()).throw(RuntimeError("customer secret")),
                max_retries=2,
            )

        event = raised.exception.event
        self.assertEqual(2, event.retries)
        self.assertEqual("failed", event.task_result)
        self.assertFalse(event.success)
        self.assertNotIn("customer secret", str(event.canonical_record()))
        self.assertEqual([event], events)

    def test_retry_and_identifier_bounds_fail_before_execution(self) -> None:
        router = ModelRouter(verified_policy())
        with self.assertRaisesRegex(ValueError, "max_retries"):
            router.execute("fast", "task-1", lambda _decision: None, max_retries=3)
        with self.assertRaisesRegex(ValueError, "task id"):
            router.resolve("fast", "raw prompt with spaces")

    def test_deterministic_operation_cannot_be_routed_to_a_model(self) -> None:
        router = ModelRouter(verified_policy())
        with self.assertRaisesRegex(ValueError, "requires no_llm"):
            router.resolve(
                "fast",
                "hash.document-2",
                operation="document_hash",
            )
        decision = router.resolve(
            "no_llm",
            "hash.document-2",
            operation="document_hash",
        )
        self.assertIsNone(decision.actual_model)

    def test_executor_identity_mismatch_cannot_create_false_actual_model_metadata(self) -> None:
        router = ModelRouter(verified_policy())

        with self.assertRaises(ModelExecutionError) as raised:
            router.execute(
                "fast",
                "classify.batch-3",
                lambda _decision: TaskExecutionReceipt(
                    value="result",
                    actual_model="qwen3:4b-instruct",
                    model_digest="0edcdef34593",
                ),
            )

        self.assertEqual("failed", raised.exception.event.task_result)
        self.assertFalse(raised.exception.event.success)


if __name__ == "__main__":
    unittest.main()
