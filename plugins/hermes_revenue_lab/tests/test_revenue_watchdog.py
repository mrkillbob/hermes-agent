from __future__ import annotations

import json
import os
import signal
import tempfile
import unittest
from pathlib import Path

from hermes_revenue_lab.guard.policy import GuardDecision, WorkloadSpec
from hermes_revenue_lab.guard.watchdog import enforce_emergency_stop, run_guarded_steps
from hermes_revenue_lab.guard.workers import (
    RevenueWorkerRecord,
    load_verified_workers,
    register_worker,
)


def decision(state: str, permitted: bool, reasons=()) -> GuardDecision:
    return GuardDecision(
        state=state,
        permitted=permitted,
        reasons=tuple(reasons),
        observed_at="2026-08-21T01:00:00-07:00",
    )


def worker(root: Path, **overrides: object) -> RevenueWorkerRecord:
    values = {
        "workload_id": "builder-001",
        "pid": 4242,
        "process_start_token": "Thu Aug 21 01:00:00 2026",
        "workload_kind": "heavy_model",
        "heavy": True,
        "checkpoint_path": str(root / "checkpoints" / "builder-001.json"),
        "registered_at": "2026-08-21T08:00:00Z",
    }
    values.update(overrides)
    return RevenueWorkerRecord(**values)


class RevenueWatchdogTest(unittest.TestCase):
    def test_registry_is_private_root_contained_and_start_token_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lab"
            root.mkdir()
            registry = root / ".hermes" / "workers"
            path = register_worker(registry, worker(root), allowed_root=root)

            verified = load_verified_workers(
                registry,
                allowed_root=root,
                start_token_provider=(
                    lambda pid: "Thu Aug 21 01:00:00 2026" if pid == 4242 else None
                ),
            )

            self.assertEqual([worker(root)], verified)
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            with self.assertRaisesRegex(ValueError, "outside Revenue Lab"):
                register_worker(root.parent / "outside", worker(root), allowed_root=root)

    def test_pid_reuse_or_missing_checkpoint_prevents_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "workers"
            register_worker(registry, worker(root), allowed_root=root)
            signals = []

            stale = enforce_emergency_stop(
                decision("EMERGENCY_STOP", False, ("critical_resource_pressure",)),
                registry,
                allowed_root=root,
                start_token_provider=lambda _pid: "different process start",
                signal_sender=lambda pid, sig: signals.append((pid, sig)),
            )
            no_checkpoint = enforce_emergency_stop(
                decision("EMERGENCY_STOP", False, ("critical_resource_pressure",)),
                registry,
                allowed_root=root,
                start_token_provider=lambda _pid: "Thu Aug 21 01:00:00 2026",
                signal_sender=lambda pid, sig: signals.append((pid, sig)),
            )

            self.assertEqual([], stale)
            self.assertEqual([], no_checkpoint)
            self.assertEqual([], signals)

    def test_checkpoint_is_verified_and_receipt_written_before_exact_sigterm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "workers"
            record = worker(root)
            checkpoint = Path(record.checkpoint_path)
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text('{"completed_steps":2}\n', encoding="utf-8")
            register_worker(registry, record, allowed_root=root)
            ordering = []

            stopped = enforce_emergency_stop(
                decision("EMERGENCY_STOP", False, ("critical_resource_pressure",)),
                registry,
                allowed_root=root,
                start_token_provider=lambda _pid: record.process_start_token,
                signal_sender=lambda pid, sig: ordering.append(("signal", pid, sig)),
                receipt_writer=lambda path, value: (
                    ordering.append(("receipt", path.name)),
                    path.write_text(json.dumps(value) + "\n", encoding="utf-8"),
                )[1],
            )

            self.assertEqual([record], stopped)
            self.assertEqual("receipt", ordering[0][0])
            self.assertEqual(("signal", 4242, signal.SIGTERM), ordering[1])
            self.assertEqual('{"completed_steps":2}\n', checkpoint.read_text())

    def test_non_emergency_decision_never_signals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "workers"
            record = worker(root)
            checkpoint = Path(record.checkpoint_path)
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text("{}\n", encoding="utf-8")
            register_worker(registry, record, allowed_root=root)
            signals = []

            stopped = enforce_emergency_stop(
                decision("PAUSED", False, ("luna_active",)),
                registry,
                allowed_root=root,
                start_token_provider=lambda _pid: record.process_start_token,
                signal_sender=lambda pid, sig: signals.append((pid, sig)),
            )

            self.assertEqual([], stopped)
            self.assertEqual([], signals)

    def test_periodic_guard_stops_new_steps_and_persists_checkpoint(self) -> None:
        decisions = iter(
            (
                decision("FULL", True),
                decision("FULL", True),
                decision("PAUSED", False, ("luna_active",)),
            )
        )
        executed = []
        checkpoints = []
        steps = [
            lambda: executed.append("one"),
            lambda: executed.append("two"),
            lambda: executed.append("three"),
        ]

        result = run_guarded_steps(
            steps,
            WorkloadSpec("heavy_compile"),
            decision_provider=lambda _workload: next(decisions),
            checkpoint_writer=lambda completed, current: checkpoints.append(
                (completed, current.state, current.reasons)
            ),
        )

        self.assertEqual(["one", "two"], executed)
        self.assertEqual("blocked", result["status"])
        self.assertEqual(2, result["completed_steps"])
        self.assertEqual((2, "PAUSED", ("luna_active",)), checkpoints[-1])

    def test_watchdog_source_has_no_broad_or_luna_termination_path(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "hermes_revenue_lab" / "guard"
        source = "\n".join(path.read_text() for path in root.glob("*.py"))
        self.assertNotIn("pkill", source)
        self.assertNotIn("killall", source)
        self.assertNotIn("stop_luna", source)
        self.assertNotIn("restart_luna", source)


if __name__ == "__main__":
    unittest.main()
