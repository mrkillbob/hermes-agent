"""Deterministic state policy for Revenue Lab resource admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo


GuardState = Literal["FULL", "LIMITED", "PAUSED", "EMERGENCY_STOP"]
WorkloadKind = Literal[
    "guard_check",
    "deterministic",
    "fast_model",
    "heavy_model",
    "image_video",
    "heavy_compile",
    "browser_swarm",
]
PACIFIC = ZoneInfo("America/Los_Angeles")
PROTECTED_START = time(5, 45)
PROTECTED_END = time(13, 30)


@dataclass(frozen=True)
class RevenueSnapshot:
    luna_process_count: int | None
    revenue_worker_count: int | None
    load_1m: float | None
    cpu_count: int | None
    memory_free_percent: float | None
    swap_used_bytes: int | None
    swap_total_bytes: int | None
    swap_delta_bytes: int | None
    memory_pressure_available: bool | None
    foreign_ollama_model_count: int | None
    luna_health_status: Literal["healthy", "unhealthy", "unavailable"]
    luna_health_latency_ms: float | None


@dataclass(frozen=True)
class WorkloadSpec:
    kind: WorkloadKind
    parameter_billions: float | None = None

    def __post_init__(self) -> None:
        allowed = {
            "guard_check",
            "deterministic",
            "fast_model",
            "heavy_model",
            "image_video",
            "heavy_compile",
            "browser_swarm",
        }
        if self.kind not in allowed:
            raise ValueError(f"unknown Revenue Lab workload kind {self.kind}")
        if self.kind in {"fast_model", "heavy_model"}:
            if self.parameter_billions is None or self.parameter_billions <= 0:
                raise ValueError("model workloads require positive parameter evidence")
        elif self.parameter_billions is not None:
            raise ValueError("non-model workload cannot contain model parameters")


@dataclass(frozen=True)
class GuardDecision:
    state: GuardState
    permitted: bool
    reasons: tuple[str, ...]
    observed_at: str


def _protected(now: datetime) -> bool:
    local = now.astimezone(PACIFIC)
    return (
        local.weekday() < 5
        and PROTECTED_START <= local.time().replace(tzinfo=None) < PROTECTED_END
    )


def _missing(snapshot: RevenueSnapshot) -> bool:
    required = (
        snapshot.luna_process_count,
        snapshot.revenue_worker_count,
        snapshot.load_1m,
        snapshot.cpu_count,
        snapshot.memory_free_percent,
        snapshot.swap_used_bytes,
        snapshot.swap_total_bytes,
        snapshot.swap_delta_bytes,
        snapshot.foreign_ollama_model_count,
    )
    return (
        any(value is None for value in required)
        or snapshot.memory_pressure_available is not True
    )


def _decision(
    state: GuardState,
    permitted: bool,
    reasons: tuple[str, ...],
    observed: datetime,
) -> GuardDecision:
    return GuardDecision(state, permitted, reasons, observed.astimezone(PACIFIC).isoformat())


def evaluate_revenue_guard(
    snapshot: RevenueSnapshot,
    workload: WorkloadSpec,
    *,
    now: datetime | None = None,
) -> GuardDecision:
    observed = now or datetime.now(PACIFIC)
    if _missing(snapshot):
        return _decision("PAUSED", False, ("resource_evidence_unavailable",), observed)
    assert snapshot.memory_free_percent is not None
    assert snapshot.load_1m is not None
    assert snapshot.cpu_count is not None
    assert snapshot.swap_used_bytes is not None
    assert snapshot.swap_total_bytes is not None
    assert snapshot.swap_delta_bytes is not None
    swap_ratio = (
        snapshot.swap_used_bytes / snapshot.swap_total_bytes
        if snapshot.swap_total_bytes > 0
        else 0.0
    )
    critical = (
        snapshot.memory_free_percent < 10.0
        or snapshot.load_1m > snapshot.cpu_count * 1.5
        or (swap_ratio > 0.5 and snapshot.memory_free_percent < 20.0)
        or (
            snapshot.swap_delta_bytes > 512 * 1024 * 1024
            and snapshot.memory_free_percent < 20.0
        )
    )
    if critical:
        permitted = workload.kind == "guard_check"
        return _decision(
            "EMERGENCY_STOP",
            permitted,
            ("critical_resource_pressure",),
            observed,
        )
    pause_reasons: list[str] = []
    if snapshot.luna_process_count and snapshot.luna_process_count > 0:
        pause_reasons.append("luna_active")
    if _protected(observed):
        pause_reasons.append("protected_market_window")
    if snapshot.revenue_worker_count and snapshot.revenue_worker_count > 0:
        pause_reasons.append("revenue_worker_active")
    if snapshot.luna_health_status == "unhealthy":
        pause_reasons.append("luna_health_unhealthy")
    if pause_reasons:
        return _decision(
            "PAUSED",
            workload.kind == "guard_check",
            tuple(pause_reasons),
            observed,
        )
    limited_reasons: list[str] = []
    if snapshot.memory_free_percent < 35.0:
        limited_reasons.append("reduced_free_memory")
    if snapshot.load_1m > snapshot.cpu_count * 0.75:
        limited_reasons.append("elevated_load")
    if swap_ratio > 0.1:
        limited_reasons.append("swap_pressure")
    if snapshot.foreign_ollama_model_count and snapshot.foreign_ollama_model_count > 0:
        limited_reasons.append("foreign_ollama_model_loaded")
    if limited_reasons:
        permitted = workload.kind in {"guard_check", "deterministic"}
        if workload.kind == "fast_model":
            permitted = (
                workload.parameter_billions is not None
                and workload.parameter_billions < 12.0
                and snapshot.foreign_ollama_model_count == 0
            )
        if not permitted:
            limited_reasons.append("workload_requires_full")
        return _decision("LIMITED", permitted, tuple(limited_reasons), observed)
    return _decision("FULL", True, (), observed)
