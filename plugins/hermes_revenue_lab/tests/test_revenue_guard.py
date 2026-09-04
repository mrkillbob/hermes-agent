from __future__ import annotations

import unittest
from datetime import datetime
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from hermes_revenue_lab.guard.collector import (
    LunaHealthObservation,
    collect_revenue_snapshot,
    probe_luna_health,
)
from hermes_revenue_lab.guard.policy import (
    RevenueSnapshot,
    WorkloadSpec,
    evaluate_revenue_guard,
)
from hermes_revenue_lab.models.benchmark_guard import GuardSnapshot
from plugins.hermes_revenue_lab.scripts import revenue_guard


PACIFIC = ZoneInfo("America/Los_Angeles")


def snapshot(**overrides: object) -> RevenueSnapshot:
    values = {
        "luna_process_count": 0,
        "revenue_worker_count": 0,
        "load_1m": 2.0,
        "cpu_count": 16,
        "memory_free_percent": 70.0,
        "swap_used_bytes": 0,
        "swap_total_bytes": 0,
        "swap_delta_bytes": 0,
        "memory_pressure_available": True,
        "foreign_ollama_model_count": 0,
        "luna_health_status": "unavailable",
        "luna_health_latency_ms": None,
    }
    values.update(overrides)
    return RevenueSnapshot(**values)


FAST = WorkloadSpec("fast_model", parameter_billions=4.7)
HEAVY = WorkloadSpec("heavy_model", parameter_billions=30.5)
DETERMINISTIC = WorkloadSpec("deterministic")


class RevenueGuardTest(unittest.TestCase):
    def test_active_luna_pauses_every_workload_outside_market_window(self) -> None:
        overnight = datetime(2026, 8, 21, 1, 0, tzinfo=PACIFIC)
        for workload in (DETERMINISTIC, FAST, HEAVY, WorkloadSpec("browser_swarm")):
            decision = evaluate_revenue_guard(
                snapshot(luna_process_count=1), workload, now=overnight
            )
            self.assertEqual("PAUSED", decision.state)
            self.assertFalse(decision.permitted)
            self.assertIn("luna_active", decision.reasons)

    def test_protected_weekday_window_pauses_without_using_clock_as_inactivity_proof(self) -> None:
        protected = datetime(2026, 8, 21, 9, 30, tzinfo=PACIFIC)
        decision = evaluate_revenue_guard(snapshot(), DETERMINISTIC, now=protected)
        self.assertEqual("PAUSED", decision.state)
        self.assertIn("protected_market_window", decision.reasons)

    def test_missing_required_host_evidence_fails_closed(self) -> None:
        decision = evaluate_revenue_guard(
            snapshot(memory_free_percent=None),
            FAST,
            now=datetime(2026, 8, 21, 1, 0, tzinfo=PACIFIC),
        )
        self.assertEqual("PAUSED", decision.state)
        self.assertFalse(decision.permitted)
        self.assertEqual(("resource_evidence_unavailable",), decision.reasons)

    def test_critical_pressure_is_emergency_stop_before_other_states(self) -> None:
        decision = evaluate_revenue_guard(
            snapshot(memory_free_percent=8.0, luna_process_count=1),
            HEAVY,
            now=datetime(2026, 8, 21, 9, 30, tzinfo=PACIFIC),
        )
        self.assertEqual("EMERGENCY_STOP", decision.state)
        self.assertFalse(decision.permitted)
        self.assertEqual(("critical_resource_pressure",), decision.reasons)

    def test_limited_state_allows_only_deterministic_and_small_model_work(self) -> None:
        observed = datetime(2026, 8, 21, 1, 0, tzinfo=PACIFIC)
        constrained = snapshot(memory_free_percent=30.0)
        self.assertTrue(evaluate_revenue_guard(constrained, DETERMINISTIC, now=observed).permitted)
        self.assertTrue(evaluate_revenue_guard(constrained, FAST, now=observed).permitted)
        for workload in (
            HEAVY,
            WorkloadSpec("image_video"),
            WorkloadSpec("heavy_compile"),
            WorkloadSpec("browser_swarm"),
        ):
            decision = evaluate_revenue_guard(constrained, workload, now=observed)
            self.assertEqual("LIMITED", decision.state)
            self.assertFalse(decision.permitted)

    def test_active_revenue_worker_prevents_parallel_launch(self) -> None:
        decision = evaluate_revenue_guard(
            snapshot(revenue_worker_count=1),
            FAST,
            now=datetime(2026, 8, 21, 1, 0, tzinfo=PACIFIC),
        )
        self.assertEqual("PAUSED", decision.state)
        self.assertIn("revenue_worker_active", decision.reasons)

    def test_foreign_loaded_model_blocks_models_but_not_deterministic_work(self) -> None:
        observed = datetime(2026, 8, 21, 1, 0, tzinfo=PACIFIC)
        occupied = snapshot(foreign_ollama_model_count=1)
        model = evaluate_revenue_guard(occupied, FAST, now=observed)
        deterministic = evaluate_revenue_guard(occupied, DETERMINISTIC, now=observed)
        self.assertEqual("LIMITED", model.state)
        self.assertFalse(model.permitted)
        self.assertTrue(deterministic.permitted)

    def test_luna_health_is_subordinate_but_unhealthy_diagnostic_pauses(self) -> None:
        observed = datetime(2026, 8, 21, 1, 0, tzinfo=PACIFIC)
        unavailable = evaluate_revenue_guard(snapshot(), FAST, now=observed)
        unhealthy = evaluate_revenue_guard(
            snapshot(luna_health_status="unhealthy", luna_health_latency_ms=1500.0),
            FAST,
            now=observed,
        )
        self.assertEqual("FULL", unavailable.state)
        self.assertEqual("PAUSED", unhealthy.state)
        self.assertIn("luna_health_unhealthy", unhealthy.reasons)

    def test_growing_swap_under_low_memory_is_emergency(self) -> None:
        decision = evaluate_revenue_guard(
            snapshot(
                memory_free_percent=18.0,
                swap_used_bytes=2_000_000_000,
                swap_total_bytes=4_000_000_000,
                swap_delta_bytes=600_000_000,
            ),
            FAST,
            now=datetime(2026, 8, 21, 1, 0, tzinfo=PACIFIC),
        )
        self.assertEqual("EMERGENCY_STOP", decision.state)


class _FakeResponse:
    status = 200

    def read(self, amount: int) -> bytes:
        return b'{"status":"ok"}'[:amount]


class _FakeConnection:
    def __init__(self) -> None:
        self.request_args: tuple[object, ...] | None = None
        self.closed = False

    def request(self, *args: object) -> None:
        self.request_args = args

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse()

    def close(self) -> None:
        self.closed = True


class RevenueCollectorTest(unittest.TestCase):
    def test_collector_adapts_bounded_snapshot_and_swap_growth(self) -> None:
        base = GuardSnapshot(
            luna_process_count=2,
            revenue_model_worker_count=1,
            load_1m=3.0,
            cpu_count=12,
            memory_free_percent=61.0,
            swap_used_bytes=900,
            swap_total_bytes=2_000,
            foreign_ollama_model_count=1,
        )
        observed = collect_revenue_snapshot(
            previous_swap_used_bytes=250,
            allowed_model="qwen3.5:4b",
            base_collector=lambda **_kwargs: base,
            health_probe=lambda: LunaHealthObservation("healthy", 12.5),
        )
        self.assertEqual(650, observed.swap_delta_bytes)
        self.assertEqual(2, observed.luna_process_count)
        self.assertEqual(1, observed.revenue_worker_count)
        self.assertTrue(observed.memory_pressure_available)
        self.assertEqual("healthy", observed.luna_health_status)
        self.assertEqual(12.5, observed.luna_health_latency_ms)

    def test_health_probe_is_loopback_only_bounded_and_diagnostic(self) -> None:
        connection = _FakeConnection()
        captured: list[tuple[str, int, float]] = []

        def factory(host: str, port: int, timeout: float) -> _FakeConnection:
            captured.append((host, port, timeout))
            return connection

        observed = probe_luna_health(connection_factory=factory, clock=lambda: 1.0)
        self.assertEqual([("127.0.0.1", 8787, 0.75)], captured)
        self.assertEqual(("GET", "/health"), connection.request_args)
        self.assertEqual("healthy", observed.status)
        self.assertEqual(0.0, observed.latency_ms)
        self.assertTrue(connection.closed)

    def test_health_probe_failure_remains_unavailable(self) -> None:
        def fail(_host: str, _port: int, _timeout: float) -> _FakeConnection:
            raise OSError("connection refused")

        observed = probe_luna_health(connection_factory=fail)
        self.assertEqual(LunaHealthObservation("unavailable", None), observed)

    def test_cli_emits_only_sanitized_snapshot_and_decision_json(self) -> None:
        overnight = datetime(2026, 8, 21, 1, 0, tzinfo=PACIFIC)
        output = StringIO()
        with (
            patch.object(revenue_guard, "collect_revenue_snapshot", return_value=snapshot()),
            patch.object(revenue_guard, "_now", return_value=overnight),
            patch("sys.stdout", output),
        ):
            code = revenue_guard.main(["--workload", "fast_model", "--parameters", "4.7"])
        self.assertEqual(0, code)
        rendered = output.getvalue()
        self.assertIn('"state": "FULL"', rendered)
        self.assertIn('"permitted": true', rendered)
        self.assertNotIn("processes", rendered)
        self.assertNotIn("command", rendered)
        self.assertNotIn("argv", rendered)


if __name__ == "__main__":
    unittest.main()
