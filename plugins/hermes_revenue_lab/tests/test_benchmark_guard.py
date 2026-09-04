from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import hermes_revenue_lab.models.benchmark_guard as benchmark_guard_module
from hermes_revenue_lab.inventory.types import CommandResult
from hermes_revenue_lab.models.benchmark_guard import (
    GuardSnapshot,
    collect_guard_snapshot,
    evaluate_benchmark_guard,
    execute_if_permitted,
)


PACIFIC = ZoneInfo("America/Los_Angeles")


def quiet_snapshot(**overrides: object) -> GuardSnapshot:
    values = {
        "luna_process_count": 0,
        "revenue_model_worker_count": 0,
        "load_1m": 2.0,
        "cpu_count": 12,
        "memory_free_percent": 55.0,
        "swap_used_bytes": 0,
        "swap_total_bytes": 10_000_000_000,
    }
    values.update(overrides)
    return GuardSnapshot(**values)


class BenchmarkGuardTest(unittest.TestCase):
    def test_release_uses_exact_inventory_model_without_shell(self) -> None:
        """Catches cleanup targeting the wrong model or bypassing argv-only execution."""
        release = getattr(benchmark_guard_module, "release_benchmark_model", None)
        self.assertTrue(callable(release), "benchmark model release boundary is unavailable")
        observed = None

        def command_result(spec):
            nonlocal observed
            observed = spec
            return CommandResult(spec.name, "available", 0, "", "", 0.01)

        with patch(
            "hermes_revenue_lab.models.benchmark_guard.run_command",
            side_effect=command_result,
        ):
            self.assertTrue(release("qwen3.5:4b"))

        self.assertEqual(
            ("/usr/local/bin/ollama", "stop", "qwen3.5:4b"),
            observed.argv,
        )

    def test_snapshot_counts_only_loaded_models_other_than_candidate(self) -> None:
        """Catches the collector ignoring a foreign loaded model or blocking its own candidate."""
        outputs = {
            "luna_processes": "",
            "revenue_processes": "",
            "uptime": "14:00 up 1 day, load averages: 2.00 1.00 1.00\n",
            "memory": "System-wide memory free percentage: 55%\n",
            "swap": "vm.swapusage: total = 1024.00M used = 0.00M free = 1024.00M\n",
            "ollama_ps": (
                "NAME ID SIZE PROCESSOR CONTEXT UNTIL\n"
                "qwen3-coder:30b 06c1097 44 GB 100% GPU 262144 4 minutes from now\n"
                "qwen3.5:4b 2a654d9 11 GB 100% GPU 262144 4 minutes from now\n"
            ),
        }

        def command_result(spec):
            return CommandResult(spec.name, "available", 0, outputs[spec.name], "", 0.01)

        with patch(
            "hermes_revenue_lab.models.benchmark_guard.run_command",
            side_effect=command_result,
        ):
            try:
                snapshot = collect_guard_snapshot(allowed_model="qwen3.5:4b")
            except TypeError as exc:
                self.fail(f"collector has no candidate-aware ownership boundary: {exc}")

        self.assertEqual(snapshot.foreign_ollama_model_count, 1)

    def test_snapshot_uses_targeted_process_evidence_beyond_full_table_truncation(self) -> None:
        """Catches a low-PID LunaBot app disappearing behind a truncated process table."""
        outputs = {
            "luna_processes": (
                "921 /Users/example/LunaBot-default/desktop/macos/TradingBotV18/build/"
                "LunaBot.app/Contents/MacOS/LunaBot\n"
            ),
            "revenue_processes": "",
            "uptime": "14:00 up 1 day, load averages: 2.00 1.00 1.00\n",
            "memory": "System-wide memory free percentage: 55%\n",
            "swap": "vm.swapusage: total = 1024.00M used = 0.00M free = 1024.00M\n",
            "ollama_ps": "NAME ID SIZE PROCESSOR CONTEXT UNTIL\n",
        }

        def command_result(spec):
            return CommandResult(spec.name, "available", 0, outputs[spec.name], "", 0.01)

        with patch(
            "hermes_revenue_lab.models.benchmark_guard.run_command",
            side_effect=command_result,
        ):
            observed = collect_guard_snapshot()

        self.assertEqual(1, observed.luna_process_count)

    def test_foreign_loaded_ollama_model_pauses_before_transport(self) -> None:
        """Catches a loaded foreign model pushing an otherwise permitted call into pressure."""
        snapshot = quiet_snapshot()
        object.__setattr__(snapshot, "foreign_ollama_model_count", 1)

        decision = evaluate_benchmark_guard(
            snapshot,
            candidate_parameter_billions=4.7,
            now=datetime(2026, 8, 20, 22, 0, tzinfo=PACIFIC),
        )

        self.assertEqual(decision.state, "PAUSED")
        self.assertFalse(decision.permitted)
        self.assertIn("foreign_ollama_model_loaded", decision.reasons)

    def test_active_luna_pauses_even_outside_market_window(self) -> None:
        decision = evaluate_benchmark_guard(
            quiet_snapshot(luna_process_count=1),
            candidate_parameter_billions=4.7,
            now=datetime(2026, 8, 20, 22, 0, tzinfo=PACIFIC),
        )
        self.assertEqual(decision.state, "PAUSED")
        self.assertFalse(decision.permitted)
        self.assertIn("luna_active", decision.reasons)

    def test_weekday_protected_window_blocks_even_fast_model(self) -> None:
        decision = evaluate_benchmark_guard(
            quiet_snapshot(),
            candidate_parameter_billions=4.7,
            now=datetime(2026, 8, 20, 10, 0, tzinfo=PACIFIC),
        )
        self.assertEqual(decision.state, "PAUSED")
        self.assertFalse(decision.permitted)
        self.assertIn("protected_market_window", decision.reasons)

    def test_limited_state_allows_small_but_blocks_heavy(self) -> None:
        snapshot = quiet_snapshot(memory_free_percent=30.0)
        now = datetime(2026, 8, 20, 22, 0, tzinfo=PACIFIC)
        fast = evaluate_benchmark_guard(snapshot, 4.7, now=now)
        heavy = evaluate_benchmark_guard(snapshot, 27.8, now=now)
        self.assertEqual(fast.state, "LIMITED")
        self.assertTrue(fast.permitted)
        self.assertEqual(heavy.state, "LIMITED")
        self.assertFalse(heavy.permitted)
        self.assertIn("heavy_model_requires_full", heavy.reasons)

    def test_missing_resource_evidence_fails_closed(self) -> None:
        decision = evaluate_benchmark_guard(
            quiet_snapshot(load_1m=None),
            candidate_parameter_billions=4.7,
            now=datetime(2026, 8, 20, 22, 0, tzinfo=PACIFIC),
        )
        self.assertEqual(decision.state, "PAUSED")
        self.assertFalse(decision.permitted)
        self.assertIn("resource_evidence_unavailable", decision.reasons)

    def test_critical_pressure_is_emergency_stop(self) -> None:
        decision = evaluate_benchmark_guard(
            quiet_snapshot(memory_free_percent=7.0),
            candidate_parameter_billions=4.7,
            now=datetime(2026, 8, 20, 22, 0, tzinfo=PACIFIC),
        )
        self.assertEqual(decision.state, "EMERGENCY_STOP")
        self.assertFalse(decision.permitted)

    def test_historical_swap_occupancy_without_current_pressure_is_limited(self) -> None:
        decision = evaluate_benchmark_guard(
            quiet_snapshot(
                memory_free_percent=88.0,
                load_1m=4.0,
                swap_used_bytes=3_700_000_000,
                swap_total_bytes=5_300_000_000,
            ),
            candidate_parameter_billions=4.7,
            now=datetime(2026, 8, 20, 22, 0, tzinfo=PACIFIC),
        )
        self.assertEqual(decision.state, "LIMITED")
        self.assertTrue(decision.permitted)
        self.assertIn("swap_pressure", decision.reasons)

    def test_blocked_decision_never_calls_transport(self) -> None:
        called = False

        def transport() -> str:
            nonlocal called
            called = True
            return "must not run"

        decision = evaluate_benchmark_guard(
            quiet_snapshot(),
            candidate_parameter_billions=4.7,
            now=datetime(2026, 8, 20, 10, 0, tzinfo=PACIFIC),
        )
        result = execute_if_permitted(decision, transport)
        self.assertFalse(called)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["guard_state"], "PAUSED")


if __name__ == "__main__":
    unittest.main()
