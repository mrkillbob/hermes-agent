from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from hermes_revenue_lab.models.benchmark import (
    benchmark_candidate,
    candidate_roles,
    load_inventory_candidates,
    run_benchmark_suite,
)
from hermes_revenue_lab.models.benchmark_guard import GuardDecision
from hermes_revenue_lab.models.corpus import benchmark_corpus
from hermes_revenue_lab.models.selection import select_models
from hermes_revenue_lab.models.types import ModelCandidate, OllamaTaskResponse


def candidate(name: str, billions: float, digest: str = "digest") -> ModelCandidate:
    return ModelCandidate(
        name=name,
        digest=digest,
        parameters=f"{billions}B",
        parameter_billions=billions,
        quantization="Q4_K_M",
        size="3 GB",
        capabilities=("completion", "tools"),
        inventory_id="inventory-1",
    )


def two_fast_inventory() -> dict[str, object]:
    return {
        "inventory_id": "inventory-1",
        "ollama": {
            "installed_models": {
                "status": "available",
                "value": [
                    {
                        "name": "qwen3.5:4b",
                        "digest": "fast-a",
                        "parameters": "4.7B",
                        "quantization": "Q4_K_M",
                        "size": "3.4 GB",
                        "capabilities": ["completion", "tools", "thinking"],
                    },
                    {
                        "name": "qwen3:4b-instruct",
                        "digest": "fast-b",
                        "parameters": "4.0B",
                        "quantization": "Q4_K_M",
                        "size": "2.5 GB",
                        "capabilities": ["completion", "tools"],
                    },
                ],
            }
        },
    }


def successful_response(task) -> OllamaTaskResponse:
    tool_call = None
    if task.family == "select_tool":
        tool_call = {
            "name": "store_candidate",
            "arguments": {"candidate_id": "C-17", "score": 4},
        }
    return OllamaTaskResponse(
        response_text=task.expected_json,
        thinking_text="",
        tool_call=tool_call,
        wall_time_seconds=1.0,
        time_to_first_token_seconds=0.1,
        prompt_eval_count=10,
        eval_count=10,
        load_duration_seconds=0.1,
        prompt_eval_duration_seconds=0.1,
        eval_duration_seconds=0.5,
        total_duration_seconds=0.8,
        tokens_per_second=20.0,
    )


class ModelBenchmarkTest(unittest.TestCase):
    def test_selection_prefers_measured_memory_efficiency_over_parameter_label(self) -> None:
        """Catches a smaller parameter label winning despite triple the measured RSS."""
        candidates = (
            candidate("resource-efficient", 4.7, "a"),
            candidate("parameter-smaller", 4.0, "b"),
        )
        roles = {"resource-efficient": ("fast",), "parameter-smaller": ("fast",)}
        records = []
        for model_name, wall, peak_rss in (
            ("resource-efficient", 7.4, 14_000_000_000),
            ("parameter-smaller", 6.5, 42_000_000_000),
        ):
            for index in range(5):
                records.append(
                    {
                        "model": model_name,
                        "task_id": f"fast-{index}",
                        "role": "fast",
                        "status": "completed",
                        "success": True,
                        "structured_valid": True,
                        "wall_time_seconds": wall,
                        "peak_ollama_rss_bytes": peak_rss,
                    }
                )

        selections = select_models(candidates, roles, records, inventory_id="inventory-1")

        self.assertEqual("resource-efficient", selections["tiers"]["fast"]["model"])
        self.assertEqual(14_000_000_000, selections["tiers"]["fast"]["peak_ollama_rss_bytes"])

    def test_failed_candidate_release_aborts_before_next_candidate(self) -> None:
        """Catches a cleanup failure being ignored before another model is launched."""
        try:
            document, _selection = run_benchmark_suite(
                two_fast_inventory(),
                requested_roles=("fast",),
                decision_provider=lambda _item: GuardDecision(
                    "FULL", True, (), "2026-08-21T00:00:00-07:00"
                ),
                transport=lambda _model, task: successful_response(task),
                candidate_releaser=lambda _item: False,
            )
        except TypeError as exc:
            self.fail(f"suite has no candidate release boundary: {exc}")

        self.assertEqual(document["status"], "partial")
        self.assertEqual(5, len(document["records"]))
        self.assertEqual("failed", document["cleanup_events"][0]["status"])

    def test_suite_releases_measured_candidate_before_starting_next_candidate(self) -> None:
        """Catches one suite-owned model remaining resident and blocking the next candidate."""
        loaded_model: str | None = None

        def decision_provider(item):
            if loaded_model is not None and loaded_model != item.name:
                return GuardDecision(
                    "PAUSED",
                    False,
                    ("foreign_ollama_model_loaded",),
                    "2026-08-21T00:00:00-07:00",
                )
            return GuardDecision("FULL", True, (), "2026-08-21T00:00:00-07:00")

        def transport(model, task):
            nonlocal loaded_model
            loaded_model = model
            return successful_response(task)

        def release_candidate(item):
            nonlocal loaded_model
            if loaded_model != item.name:
                return False
            loaded_model = None
            return True

        try:
            document, _selection = run_benchmark_suite(
                two_fast_inventory(),
                requested_roles=("fast",),
                decision_provider=decision_provider,
                transport=transport,
                candidate_releaser=release_candidate,
            )
        except TypeError as exc:
            self.fail(f"suite has no candidate release boundary: {exc}")

        self.assertEqual(document["status"], "completed")
        self.assertEqual(10, len(document["records"]))
        self.assertTrue(all(row["status"] == "completed" for row in document["records"]))
        self.assertEqual(
            ["released", "released"],
            [event["status"] for event in document["cleanup_events"]],
        )

    def test_protected_suite_publishes_blocked_evidence_without_transport(self) -> None:
        inventory = {
            "inventory_id": "inventory-1",
            "ollama": {
                "installed_models": {
                    "status": "available",
                    "value": [
                        {
                            "name": "qwen3.5:4b",
                            "digest": "fast",
                            "parameters": "4.7B",
                            "quantization": "Q4_K_M",
                            "size": "3.4 GB",
                            "capabilities": ["completion", "tools", "thinking"],
                        }
                    ],
                }
            },
        }
        called = False

        def transport(_model, _task):
            nonlocal called
            called = True
            raise AssertionError("transport must not run")

        decision = GuardDecision(
            "PAUSED",
            False,
            ("protected_market_window",),
            "2026-08-20T10:00:00-07:00",
        )
        result, selection = run_benchmark_suite(
            inventory,
            requested_roles=("fast", "standard"),
            decision_provider=lambda _candidate: decision,
            transport=transport,
        )
        self.assertFalse(called)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(result["records"]), 5)
        self.assertTrue(all(row["status"] == "blocked" for row in result["records"]))
        self.assertEqual(selection["tiers"]["fast"]["status"], "unavailable")

    def test_inventory_loader_and_roles_do_not_promote_nearby_large_models(self) -> None:
        inventory = {
            "inventory_id": "inventory-1",
            "ollama": {
                "installed_models": {
                    "status": "available",
                    "value": [
                        {
                            "name": "qwen3.5:4b",
                            "digest": "fast",
                            "parameters": "4.7B",
                            "quantization": "Q4_K_M",
                            "size": "3.4 GB",
                            "capabilities": ["completion", "tools", "thinking"],
                        },
                        {
                            "name": "qwen3-coder:30b",
                            "digest": "coder",
                            "parameters": "30.5B",
                            "quantization": "Q4_K_M",
                            "size": "18 GB",
                            "capabilities": ["completion", "tools"],
                        },
                        {
                            "name": "qwen3-coder-next:q4_K_M",
                            "digest": "huge",
                            "parameters": "79.7B",
                            "quantization": "Q4_K_M",
                            "size": "51 GB",
                            "capabilities": ["completion", "tools"],
                        },
                    ],
                }
            },
        }
        candidates = load_inventory_candidates(inventory)
        roles = candidate_roles(candidates)
        self.assertEqual(len(candidates), 3)
        self.assertEqual(roles["qwen3.5:4b"], ("fast",))
        self.assertEqual(roles["qwen3-coder:30b"], ("coding",))
        self.assertEqual(roles["qwen3-coder-next:q4_K_M"], ())

    def test_blocked_candidate_never_reaches_transport(self) -> None:
        called = False

        def transport(_model, _task):
            nonlocal called
            called = True
            raise AssertionError("transport must not run")

        decision = GuardDecision(
            state="PAUSED",
            permitted=False,
            reasons=("protected_market_window",),
            observed_at="2026-08-20T10:00:00-07:00",
        )
        records = benchmark_candidate(
            candidate("qwen3.5:4b", 4.7),
            [next(task for task in benchmark_corpus() if task.family == "decide_escalation")],
            decision_provider=lambda _candidate: decision,
            transport=transport,
        )
        self.assertFalse(called)
        self.assertEqual(records[0]["status"], "blocked")
        self.assertNotIn("response_text", records[0])

    def test_successful_record_contains_metrics_and_hash_not_raw_response(self) -> None:
        item = next(task for task in benchmark_corpus() if task.family == "decide_escalation")
        decision = GuardDecision("FULL", True, (), "2026-08-20T22:00:00-07:00")

        def transport(_model, _task):
            return OllamaTaskResponse(
                response_text=item.expected_json,
                thinking_text="",
                tool_call=None,
                wall_time_seconds=1.5,
                time_to_first_token_seconds=0.2,
                prompt_eval_count=20,
                eval_count=10,
                load_duration_seconds=0.1,
                prompt_eval_duration_seconds=0.2,
                eval_duration_seconds=0.5,
                total_duration_seconds=1.0,
                tokens_per_second=20.0,
            )

        records = benchmark_candidate(
            candidate("qwen3.5:4b", 4.7),
            [item],
            decision_provider=lambda _candidate: decision,
            transport=transport,
            now=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc),
            resource_measurer=lambda callback: (
                callback(),
                {
                    "peak_ollama_rss_bytes": 5000,
                    "peak_ollama_cpu_percent": 80.0,
                    "gpu_pressure": None,
                },
            ),
        )
        record = records[0]
        self.assertEqual(record["status"], "completed")
        self.assertTrue(record["success"])
        self.assertEqual(record["tokens_per_second"], 20.0)
        self.assertEqual(len(record["response_sha256"]), 64)
        self.assertNotIn("response_text", record)
        self.assertEqual(record["peak_ollama_rss_bytes"], 5000)
        self.assertEqual(record["peak_ollama_cpu_percent"], 80.0)

    def test_selection_uses_parameter_count_only_after_resource_and_latency_ties(self) -> None:
        candidates = (
            candidate("fast-small", 4.0, "a"),
            candidate("fast-large", 5.0, "b"),
        )
        roles = {"fast-small": ("fast",), "fast-large": ("fast",)}
        records = []
        for model_name in ("fast-small", "fast-large"):
            for index in range(5):
                records.append(
                    {
                        "model": model_name,
                        "task_id": f"fast-{index}",
                        "role": "fast",
                        "status": "completed",
                        "success": True,
                        "structured_valid": True,
                        "wall_time_seconds": 1.0,
                        "peak_ollama_rss_bytes": 5_000_000_000,
                    }
                )
        selections = select_models(candidates, roles, records, inventory_id="inventory-1")
        self.assertEqual(selections["tiers"]["fast"]["model"], "fast-small")
        self.assertEqual(selections["tiers"]["standard"]["status"], "unavailable")
        self.assertEqual(selections["tiers"]["reasoning"]["status"], "unavailable")
        self.assertEqual(selections["tiers"]["escalation"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
