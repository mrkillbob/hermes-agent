from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from hermes_revenue_lab.routing_learning import (
    ModelObservation,
    RoutingLearningStore,
    recommend_task_routes,
    summarize_model_observations,
)


def observation(
    observation_id: str,
    *,
    task_type: str = "normalize_candidate",
    model: str = "qwen3.5:4b",
    digest: str = "2a654d98e6fb",
    latency: str = "10",
    profit: Decimal | None = Decimal(2),
) -> ModelObservation:
    return ModelObservation(
        observation_id=observation_id,
        task_type=task_type,
        provider="ollama-launch",
        model=model,
        model_digest=digest,
        latency_seconds=Decimal(latency),
        compute_seconds=Decimal(latency),
        success=True,
        review_score=Decimal("0.90"),
        retries=0,
        escalated=False,
        final_outcome="useful",
        profit_usd=profit,
        source_ref=f"artifact:{observation_id}/model_usage.json",
        recorded_at="2026-08-21T12:00:00+00:00",
    )


class RoutingLearningTest(unittest.TestCase):
    def test_store_is_private_root_contained_and_write_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RoutingLearningStore(
                root / "private" / "routing.sqlite3", allowed_root=root
            )
            value = observation("obs-001")
            store.record(value)
            self.assertEqual((value,), store.observations())
            self.assertEqual(0o600, store.database.stat().st_mode & 0o777)
            with self.assertRaisesRegex(ValueError, "already exists"):
                store.record(value)
            with self.assertRaisesRegex(ValueError, "outside"):
                RoutingLearningStore(
                    root / "outside.sqlite3", allowed_root=root / "private"
                )

    def test_summary_uses_exact_identity_and_wall_clock_throughput(self) -> None:
        rows = (observation("obs-001"), observation("obs-002"))
        summary = summarize_model_observations(rows)[0]
        self.assertEqual(
            ("ollama-launch", "qwen3.5:4b", "2a654d98e6fb"), summary.model_identity
        )
        self.assertEqual(2, summary.sample_size)
        self.assertEqual(Decimal("0.1"), summary.useful_outputs_per_wall_clock_second)
        self.assertEqual(Decimal(720), summary.profit_per_compute_hour_usd)

    def test_unknown_profit_keeps_profit_per_compute_hour_unknown(self) -> None:
        rows = (observation("obs-001"), observation("obs-002", profit=None))
        summary = summarize_model_observations(rows)[0]
        self.assertIsNone(summary.profit_per_compute_hour_usd)
        self.assertEqual(1, summary.profit_observation_count)

    def test_slow_larger_model_is_a_regression_for_same_task(self) -> None:
        fast = tuple(observation(f"fast-{index}") for index in range(3))
        slow = tuple(
            observation(
                f"slow-{index}",
                model="qwen3.5:27b",
                digest="abcdef123456",
                latency="300",
            )
            for index in range(3)
        )
        recommendation = recommend_task_routes(fast + slow, minimum_samples=3)[0]
        self.assertEqual("qwen3.5:4b", recommendation.model)
        self.assertEqual(
            "useful_output_per_wall_clock_second", recommendation.objective
        )

    def test_recommendations_are_task_specific_and_sample_gated(self) -> None:
        rows = tuple(observation(f"norm-{index}") for index in range(3)) + (
            observation("code-001", task_type="automation_code"),
        )
        results = {
            item.task_type: item
            for item in recommend_task_routes(rows, minimum_samples=3)
        }
        self.assertEqual("recommended", results["normalize_candidate"].status)
        self.assertEqual("insufficient_evidence", results["automation_code"].status)
        self.assertIsNone(results["automation_code"].model)

    def test_review_retry_escalation_and_outcome_are_retained(self) -> None:
        value = replace(
            observation("obs-001"),
            success=False,
            review_score=Decimal("0.25"),
            retries=2,
            escalated=True,
            final_outcome="rejected",
        )
        summary = summarize_model_observations((value,))[0]
        self.assertEqual(Decimal(0), summary.success_rate)
        self.assertEqual(Decimal(1), summary.retry_rate)
        self.assertEqual(Decimal(1), summary.escalation_rate)
        self.assertEqual(Decimal("0.25"), summary.mean_review_score)


if __name__ == "__main__":
    unittest.main()
