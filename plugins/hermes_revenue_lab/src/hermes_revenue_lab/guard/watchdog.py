"""Periodic admission checks and exact Revenue-owned emergency interruption."""

from __future__ import annotations

import os
import signal
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TypeVar

from .policy import GuardDecision, WorkloadSpec
from .workers import (
    RevenueWorkerRecord,
    load_verified_workers,
    process_start_token,
    write_stop_receipt,
)


_T = TypeVar("_T")


def enforce_emergency_stop(
    decision: GuardDecision,
    registry_directory: Path,
    *,
    allowed_root: Path,
    start_token_provider: Callable[[int], str | None] = process_start_token,
    signal_sender: Callable[[int, int], None] = os.kill,
    receipt_writer: Callable[[Path, object], None] = write_stop_receipt,
) -> list[RevenueWorkerRecord]:
    if decision.state != "EMERGENCY_STOP":
        return []
    records = load_verified_workers(
        registry_directory,
        allowed_root=allowed_root,
        start_token_provider=start_token_provider,
    )
    stopped: list[RevenueWorkerRecord] = []
    for record in records:
        if not record.heavy:
            continue
        checkpoint = Path(record.checkpoint_path).resolve(strict=False)
        try:
            checkpoint.relative_to(allowed_root.resolve(strict=True))
        except ValueError:
            continue
        if (
            not checkpoint.is_file()
            or checkpoint.is_symlink()
            or checkpoint.stat().st_size > 10_000_000
        ):
            continue
        receipt_path = registry_directory.resolve(strict=False) / f"{record.workload_id}.stop.json"
        receipt_writer(
            receipt_path,
            {
                "schema_version": "hrl.worker_stop.v1",
                "workload_id": record.workload_id,
                "pid": record.pid,
                "checkpoint_path": record.checkpoint_path,
                "guard": asdict(decision),
                "signal": "SIGTERM",
            },
        )
        signal_sender(record.pid, signal.SIGTERM)
        stopped.append(record)
    return stopped


def run_guarded_steps(
    steps: Sequence[Callable[[], _T]],
    workload: WorkloadSpec,
    *,
    decision_provider: Callable[[WorkloadSpec], GuardDecision],
    checkpoint_writer: Callable[[int, GuardDecision], None],
) -> dict[str, object]:
    completed = 0
    for step in steps:
        decision = decision_provider(workload)
        if not decision.permitted:
            checkpoint_writer(completed, decision)
            return {
                "status": "blocked",
                "completed_steps": completed,
                "guard_state": decision.state,
                "reason_codes": list(decision.reasons),
            }
        try:
            step()
        except Exception:
            checkpoint_writer(completed, decision)
            raise
        completed += 1
        checkpoint_writer(completed, decision)
    return {"status": "completed", "completed_steps": completed}
