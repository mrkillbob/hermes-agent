"""Bounded resource sampling around one local-model request."""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from hermes_revenue_lab.inventory.runner import run_command
from hermes_revenue_lab.inventory.types import CommandSpec


class ResourceMeasurementError(RuntimeError):
    """Raised when a required numeric sampler is unavailable."""


@dataclass(frozen=True)
class ResourceSample:
    observed_monotonic: float
    load_1m: float
    memory_free_percent: float
    swap_used_bytes: int
    swap_total_bytes: int
    ollama_runner_count: int
    ollama_cpu_percent: float
    ollama_rss_bytes: int


def parse_ollama_process_resources(text: str) -> dict[str, int | float]:
    count = 0
    cpu = 0.0
    rss_kib = 0
    for line in text.splitlines():
        fields = line.split(maxsplit=4)
        if len(fields) != 5 or not fields[0].isdigit():
            continue
        command_parts = fields[4].split()
        if len(command_parts) < 2:
            continue
        executable = Path(command_parts[0]).name.lower()
        is_runner = executable == "llama-server" or (
            executable == "ollama" and command_parts[1].lower() == "runner"
        )
        if not is_runner:
            continue
        count += 1
        cpu += float(fields[2])
        rss_kib += int(fields[3])
    return {
        "runner_count": count,
        "cpu_percent": round(cpu, 3),
        "rss_bytes": rss_kib * 1024,
    }


def _required_float(pattern: str, text: str, name: str) -> float:
    match = re.search(pattern, text, re.I)
    if not match:
        raise ResourceMeasurementError(f"{name} was unavailable")
    return float(match.group(1))


def _bytes(value: float, unit: str) -> int:
    return int(value * {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[unit.upper()])


def _swap(text: str) -> tuple[int, int]:
    total = re.search(r"total\s*=\s*([0-9.]+)([KMGT])", text, re.I)
    used = re.search(r"used\s*=\s*([0-9.]+)([KMGT])", text, re.I)
    if not total or not used:
        raise ResourceMeasurementError("swap evidence was unavailable")
    return _bytes(float(used.group(1)), used.group(2)), _bytes(
        float(total.group(1)), total.group(2)
    )


def _collect_targeted_ollama_processes() -> str:
    pids: set[int] = set()
    for spec_name, process_name in (
        ("ollama_pids", "ollama"),
        ("llama_server_pids", "llama-server"),
    ):
        result = run_command(
            CommandSpec(spec_name, ("/usr/bin/pgrep", "-x", process_name))
        )
        if result.status == "available":
            pids.update(int(line) for line in result.stdout.splitlines() if line.isdigit())
        elif result.exit_code != 1:
            raise ResourceMeasurementError(f"{spec_name} sampler was unavailable")
    if not pids:
        return ""
    if len(pids) > 32:
        raise ResourceMeasurementError("Ollama process count exceeded the sampler bound")
    result = run_command(
        CommandSpec(
            "processes",
            (
                "/bin/ps",
                "-p",
                ",".join(str(pid) for pid in sorted(pids)),
                "-o",
                "pid=,ppid=,%cpu=,rss=,command=",
            ),
        )
    )
    if result.status != "available":
        raise ResourceMeasurementError("targeted Ollama process sampler was unavailable")
    return result.stdout


def collect_resource_sample() -> ResourceSample:
    process_text = _collect_targeted_ollama_processes()
    commands = (
        CommandSpec("uptime", ("/usr/bin/uptime",)),
        CommandSpec("memory", ("/usr/bin/memory_pressure",)),
        CommandSpec("swap", ("/usr/sbin/sysctl", "vm.swapusage")),
    )
    results = {spec.name: run_command(spec) for spec in commands}
    unavailable = [name for name, result in results.items() if result.status != "available"]
    if unavailable:
        raise ResourceMeasurementError("required sampler unavailable: " + ", ".join(unavailable))
    processes = parse_ollama_process_resources(process_text)
    swap_used, swap_total = _swap(results["swap"].stdout)
    return ResourceSample(
        observed_monotonic=time.monotonic(),
        load_1m=_required_float(
            r"load averages?:\s*([0-9.]+)", results["uptime"].stdout, "load average"
        ),
        memory_free_percent=_required_float(
            r"System-wide memory free percentage:\s*([0-9.]+)%",
            results["memory"].stdout,
            "free memory percentage",
        ),
        swap_used_bytes=swap_used,
        swap_total_bytes=swap_total,
        ollama_runner_count=int(processes["runner_count"]),
        ollama_cpu_percent=float(processes["cpu_percent"]),
        ollama_rss_bytes=int(processes["rss_bytes"]),
    )


def summarize_resource_samples(samples: Sequence[ResourceSample]) -> dict[str, object]:
    if not samples:
        return {
            "sample_count": 0,
            "peak_ollama_rss_bytes": None,
            "peak_ollama_cpu_percent": None,
            "swap_used_before_bytes": None,
            "swap_used_peak_bytes": None,
            "swap_used_after_bytes": None,
            "minimum_memory_free_percent": None,
            "peak_load_1m": None,
            "gpu_pressure": None,
            "gpu_pressure_reason": "trusted sampler unavailable",
        }
    return {
        "sample_count": len(samples),
        "peak_ollama_rss_bytes": max(sample.ollama_rss_bytes for sample in samples),
        "peak_ollama_cpu_percent": max(sample.ollama_cpu_percent for sample in samples),
        "swap_used_before_bytes": samples[0].swap_used_bytes,
        "swap_used_peak_bytes": max(sample.swap_used_bytes for sample in samples),
        "swap_used_after_bytes": samples[-1].swap_used_bytes,
        "minimum_memory_free_percent": min(sample.memory_free_percent for sample in samples),
        "peak_load_1m": max(sample.load_1m for sample in samples),
        "gpu_pressure": None,
        "gpu_pressure_reason": "trusted sampler unavailable",
    }


_T = TypeVar("_T")


def measure_resource_call(
    callback: Callable[[], _T],
    *,
    sample_provider: Callable[[], ResourceSample] = collect_resource_sample,
    interval_seconds: float = 0.25,
) -> tuple[_T, dict[str, object]]:
    if interval_seconds <= 0:
        raise ValueError("resource sample interval must be positive")
    samples = [sample_provider()]
    stop = threading.Event()
    errors: list[str] = []

    def sample_loop() -> None:
        while not stop.wait(interval_seconds):
            try:
                samples.append(sample_provider())
            except Exception as exc:
                errors.append(type(exc).__name__)

    worker = threading.Thread(target=sample_loop, name="hrl-resource-sampler", daemon=True)
    worker.start()
    try:
        result = callback()
    finally:
        stop.set()
        worker.join(timeout=max(1.0, interval_seconds * 2))
    samples.append(sample_provider())
    summary = summarize_resource_samples(samples)
    summary["sampler_error_types"] = sorted(set(errors))
    summary["cpu_count"] = os.cpu_count()
    return result, summary
