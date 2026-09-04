"""Conservative preflight guard for HRL-1 model measurements."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from hermes_revenue_lab.inventory.parsers import parse_ollama_ps
from hermes_revenue_lab.inventory.runner import run_command
from hermes_revenue_lab.inventory.types import CommandResult, CommandSpec


GuardState = Literal["FULL", "LIMITED", "PAUSED", "EMERGENCY_STOP"]
PACIFIC = ZoneInfo("America/Los_Angeles")
PROTECTED_START = time(5, 45)
PROTECTED_END = time(13, 30)


@dataclass(frozen=True)
class GuardSnapshot:
    luna_process_count: int | None
    revenue_model_worker_count: int | None
    load_1m: float | None
    cpu_count: int | None
    memory_free_percent: float | None
    swap_used_bytes: int | None
    swap_total_bytes: int | None
    foreign_ollama_model_count: int | None = 0


@dataclass(frozen=True)
class GuardDecision:
    state: GuardState
    permitted: bool
    reasons: tuple[str, ...]
    observed_at: str


def _inside_protected_window(now: datetime) -> bool:
    local = now.astimezone(PACIFIC)
    return (
        local.weekday() < 5
        and PROTECTED_START <= local.time().replace(tzinfo=None) < PROTECTED_END
    )


def _missing(snapshot: GuardSnapshot) -> bool:
    return any(
        value is None
        for value in (
            snapshot.luna_process_count,
            snapshot.revenue_model_worker_count,
            snapshot.load_1m,
            snapshot.cpu_count,
            snapshot.memory_free_percent,
            snapshot.swap_used_bytes,
            snapshot.swap_total_bytes,
            snapshot.foreign_ollama_model_count,
        )
    )


def evaluate_benchmark_guard(
    snapshot: GuardSnapshot,
    candidate_parameter_billions: float,
    *,
    now: datetime | None = None,
) -> GuardDecision:
    observed = now or datetime.now(PACIFIC)
    reasons: list[str] = []

    if _missing(snapshot):
        return GuardDecision(
            state="PAUSED",
            permitted=False,
            reasons=("resource_evidence_unavailable",),
            observed_at=observed.isoformat(),
        )

    assert snapshot.memory_free_percent is not None
    assert snapshot.load_1m is not None
    assert snapshot.cpu_count is not None
    assert snapshot.swap_used_bytes is not None
    assert snapshot.swap_total_bytes is not None
    swap_ratio = (
        snapshot.swap_used_bytes / snapshot.swap_total_bytes
        if snapshot.swap_total_bytes > 0
        else 0.0
    )

    if (
        snapshot.memory_free_percent < 10.0
        or snapshot.load_1m > snapshot.cpu_count * 1.5
        or (swap_ratio > 0.5 and snapshot.memory_free_percent < 20.0)
    ):
        return GuardDecision(
            state="EMERGENCY_STOP",
            permitted=False,
            reasons=("critical_resource_pressure",),
            observed_at=observed.isoformat(),
        )

    if snapshot.luna_process_count and snapshot.luna_process_count > 0:
        reasons.append("luna_active")
    if _inside_protected_window(observed):
        reasons.append("protected_market_window")
    if snapshot.revenue_model_worker_count and snapshot.revenue_model_worker_count > 0:
        reasons.append("revenue_model_worker_active")
    if snapshot.foreign_ollama_model_count and snapshot.foreign_ollama_model_count > 0:
        reasons.append("foreign_ollama_model_loaded")
    if reasons:
        return GuardDecision("PAUSED", False, tuple(reasons), observed.isoformat())

    limited_reasons: list[str] = []
    if snapshot.memory_free_percent < 35.0:
        limited_reasons.append("reduced_free_memory")
    if snapshot.load_1m > snapshot.cpu_count * 0.75:
        limited_reasons.append("elevated_load")
    if swap_ratio > 0.1:
        limited_reasons.append("swap_pressure")
    if limited_reasons:
        permitted = candidate_parameter_billions < 12.0
        if not permitted:
            limited_reasons.append("heavy_model_requires_full")
        return GuardDecision("LIMITED", permitted, tuple(limited_reasons), observed.isoformat())

    return GuardDecision("FULL", True, (), observed.isoformat())


_T = TypeVar("_T")


def execute_if_permitted(
    decision: GuardDecision,
    callback: Callable[[], _T],
) -> _T | Mapping[str, object]:
    if not decision.permitted:
        return {
            "status": "blocked",
            "guard_state": decision.state,
            "reason_codes": list(decision.reasons),
            "observed_at": decision.observed_at,
        }
    return callback()


def release_benchmark_model(model_name: str) -> bool:
    """Unload one exact suite-owned Ollama model through bounded argv-only execution."""

    if not model_name or any(character.isspace() for character in model_name):
        raise ValueError("Ollama model must be a non-empty exact inventory name")
    result = run_command(
        CommandSpec(
            "ollama_stop",
            ("/usr/local/bin/ollama", "stop", model_name),
            timeout_seconds=30.0,
        )
    )
    return result.status == "available"


def _first_float(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text, re.I)
    return float(match.group(1)) if match else None


def _bytes(value: float, unit: str) -> int:
    factors = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    return int(value * factors[unit.upper()])


def _swap_values(text: str) -> tuple[int | None, int | None]:
    total = re.search(r"total\s*=\s*([0-9.]+)([KMGT])", text, re.I)
    used = re.search(r"used\s*=\s*([0-9.]+)([KMGT])", text, re.I)
    if not total or not used:
        return None, None
    return _bytes(float(used.group(1)), used.group(2)), _bytes(
        float(total.group(1)), total.group(2)
    )


def _pgrep_count(result: CommandResult, *, ignored_pid: int) -> int | None:
    if result.status != "available":
        if result.exit_code == 1 and not result.stdout.strip():
            return 0
        return None
    pids = {
        int(line.split(maxsplit=1)[0])
        for line in result.stdout.splitlines()
        if line.split(maxsplit=1) and line.split(maxsplit=1)[0].isdigit()
    }
    pids.discard(ignored_pid)
    return len(pids)


def collect_guard_snapshot(*, allowed_model: str | None = None) -> GuardSnapshot:
    """Collect a bounded no-LLM snapshot without persisting process commands."""

    commands = (
        CommandSpec(
            "luna_processes",
            (
                "/usr/bin/pgrep",
                "-fl",
                (
                    "(/LunaBot[.]app/Contents/MacOS/LunaBot|"
                    "/LunaBot-default/dashboard/run_dashboard[.]py|"
                    "/TradingBotV18/(main|live_runner)[.]py)( |$)"
                ),
            ),
        ),
        CommandSpec(
            "revenue_processes",
            (
                "/usr/bin/pgrep",
                "-fl",
                (
                    "(/HermesRevenueLab/|hermes_revenue_lab|"
                    "scripts/(run_model_benchmarks|revenue_guard)[.]py)"
                ),
            ),
        ),
        CommandSpec("uptime", ("/usr/bin/uptime",)),
        CommandSpec("memory", ("/usr/bin/memory_pressure",)),
        CommandSpec("swap", ("/usr/sbin/sysctl", "vm.swapusage")),
        CommandSpec("ollama_ps", ("/usr/local/bin/ollama", "ps")),
    )
    results = {spec.name: run_command(spec) for spec in commands}
    luna_count = _pgrep_count(results["luna_processes"], ignored_pid=os.getpid())
    revenue_count = _pgrep_count(results["revenue_processes"], ignored_pid=os.getpid())
    required = (results[name] for name in ("uptime", "memory", "swap", "ollama_ps"))
    if (
        any(result.status != "available" for result in required)
        or luna_count is None
        or revenue_count is None
    ):
        return GuardSnapshot(None, None, None, os.cpu_count(), None, None, None, None)
    loaded_models = parse_ollama_ps(results["ollama_ps"].stdout)
    swap_used, swap_total = _swap_values(results["swap"].stdout)
    return GuardSnapshot(
        luna_process_count=luna_count,
        revenue_model_worker_count=revenue_count,
        load_1m=_first_float(r"load averages?:\s*([0-9.]+)", results["uptime"].stdout),
        cpu_count=os.cpu_count(),
        memory_free_percent=_first_float(
            r"System-wide memory free percentage:\s*([0-9.]+)%",
            results["memory"].stdout,
        ),
        swap_used_bytes=swap_used,
        swap_total_bytes=swap_total,
        foreign_ollama_model_count=sum(
            1 for row in loaded_models if str(row["name"]) != allowed_model
        ),
    )
