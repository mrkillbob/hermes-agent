"""Authoritative orchestration for the HRL-0 environment inventory."""

import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .classification import classify_resource_state
from .parsers import (
    parse_df,
    parse_hardware,
    parse_hermes_cron,
    parse_hermes_profiles,
    parse_hermes_tools,
    parse_hermes_version,
    parse_ollama_list,
    parse_ollama_ps,
    parse_ollama_show,
    parse_process_table,
    parse_vm_stat,
)
from .redaction import sanitize_diagnostic
from .runner import run_command
from .types import CommandResult, CommandSpec, InventoryContext

Runner = Callable[[CommandSpec], CommandResult]

_HERMES = "/Users/mikedemott/.local/bin/hermes"
_OLLAMA = "/usr/local/bin/ollama"

BASE_COMMANDS = (
    CommandSpec("hermes_version", (_HERMES, "--version"), required=True),
    CommandSpec("hermes_tools", (_HERMES, "tools", "list")),
    CommandSpec("hermes_profiles", (_HERMES, "profile", "list")),
    CommandSpec("hermes_cron", (_HERMES, "cron", "list")),
    CommandSpec("hermes_cron_status", (_HERMES, "cron", "status")),
    CommandSpec("hermes_computer_use", (_HERMES, "computer-use", "status")),
    CommandSpec("hermes_mcp", (_HERMES, "mcp", "list")),
    CommandSpec("ollama_version", (_OLLAMA, "--version"), required=True),
    CommandSpec("ollama_list", (_OLLAMA, "list"), required=True),
    CommandSpec("hardware", ("/usr/sbin/system_profiler", "SPHardwareDataType"), required=True),
    CommandSpec("storage", ("/bin/df", "-k", "/Users/mikedemott"), required=True),
    CommandSpec("vm_stat", ("/usr/bin/vm_stat",)),
    CommandSpec("crontab", ("/usr/bin/crontab", "-l")),
    CommandSpec("launchd", ("/bin/launchctl", "list")),
)

RESOURCE_COMMANDS = (
    CommandSpec("resource_uptime", ("/usr/bin/uptime",)),
    CommandSpec("resource_memory", ("/usr/bin/memory_pressure",)),
    CommandSpec("resource_processes", ("/bin/ps", "-axo", "pid,ppid,%cpu,%mem,rss,etime,command")),
    CommandSpec("resource_ollama_ps", (_OLLAMA, "ps")),
)


def _observation(result: CommandResult, value: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "value": value if result.status == "available" else None,
        "reason": None if result.status == "available" else (result.stderr or "command unavailable"),
    }


def _load_1m(text: str) -> float | None:
    match = re.search(r"load averages?:\s*([0-9.]+)", text)
    return float(match.group(1)) if match else None


def _memory_free_percent(text: str) -> float | None:
    match = re.search(r"System-wide memory free percentage:\s*([0-9.]+)%", text)
    return float(match.group(1)) if match else None


def _ollama_version(text: str) -> str | None:
    match = re.search(r"(?:version is|version)\s+v?([0-9][^\s]*)", text, re.I)
    return match.group(1) if match else None


def _launchd_labels(text: str) -> list[str]:
    labels: list[str] = []
    for line in text.splitlines():
        fields = line.split(maxsplit=2)
        if len(fields) == 3 and re.search(r"hermes|ollama|trading|luna", fields[2], re.I):
            labels.append(fields[2])
    return sorted(set(labels))


def collect_resource_samples(
    runner: Runner,
    count: int,
    interval: float,
) -> tuple[list[dict[str, object]], list[CommandResult]]:
    samples: list[dict[str, object]] = []
    all_results: list[CommandResult] = []
    for index in range(count):
        results = {spec.name: runner(spec) for spec in RESOURCE_COMMANDS}
        all_results.extend(results.values())
        processes = parse_process_table(results["resource_processes"].stdout)
        loaded = parse_ollama_ps(results["resource_ollama_ps"].stdout)
        samples.append(
            {
                "load_1m": _load_1m(results["resource_uptime"].stdout),
                "memory_free_percent": _memory_free_percent(results["resource_memory"].stdout),
                "luna_count": processes["luna"]["count"],
                "loaded_models": len(loaded),
                "revenue_lab_workers": max(0, int(processes["revenue_lab"]["count"]) - 1),
                "process_aggregates": processes,
            }
        )
        if interval and index + 1 < count:
            time.sleep(interval)
    return samples, all_results


def _source_record(result: CommandResult) -> dict[str, object]:
    return {
        "name": result.name,
        "status": result.status,
        "exit_code": result.exit_code,
        "duration_seconds": round(result.duration_seconds, 6),
    }


def build_inventory_document(
    context: InventoryContext,
    results: Mapping[str, CommandResult],
    model_rows: Sequence[Mapping[str, str]],
    model_details: Sequence[Mapping[str, object]],
    samples: Sequence[Mapping[str, object]],
    resource_results: Sequence[CommandResult],
    isolation_verdict: Mapping[str, object] | None = None,
) -> dict[str, object]:
    resource_verdict = classify_resource_state(samples)
    required_blocked = [
        spec.name
        for spec in BASE_COMMANDS
        if spec.required and results[spec.name].status != "available"
    ]
    installed_models: list[dict[str, object]] = []
    for row, detail in zip(model_rows, model_details):
        installed_models.append({**row, **detail})
    process_latest = samples[-1].get("process_aggregates", {}) if samples else {}
    unknowns = [f"required command unavailable: {name}" for name in required_blocked]
    if resource_verdict["idle_baseline"]["status"] != "available":  # type: ignore[index]
        unknowns.append("idle resource baseline unavailable")
    if isolation_verdict is None or isolation_verdict.get("status") != "available":
        unknowns.append("write isolation not observed")
    launchd_result = results["launchd"]
    return {
        "schema_version": "hrl.environment_inventory.v1",
        "inventory_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12],
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "classification": resource_verdict["classification"],
        "workspace": {
            "path": sanitize_diagnostic(str(context.workspace)),
            "hermes_home": sanitize_diagnostic(str(context.hermes_home)),
            "tradingbot_path": sanitize_diagnostic(str(context.tradingbot_path)),
        },
        "hermes": {
            "version": _observation(results["hermes_version"], parse_hermes_version(results["hermes_version"].stdout)),
            "tools": _observation(results["hermes_tools"], parse_hermes_tools(results["hermes_tools"].stdout)),
            "profiles": _observation(results["hermes_profiles"], parse_hermes_profiles(results["hermes_profiles"].stdout)),
            "cron_jobs": _observation(results["hermes_cron"], parse_hermes_cron(results["hermes_cron"].stdout)),
            "cron_status": _observation(results["hermes_cron_status"], results["hermes_cron_status"].stdout.strip()[:1000]),
            "computer_use": _observation(results["hermes_computer_use"], results["hermes_computer_use"].stdout.strip()[:1000]),
            "mcp": _observation(results["hermes_mcp"], results["hermes_mcp"].stdout.strip()[:2000]),
        },
        "ollama": {
            "version": _observation(results["ollama_version"], _ollama_version(results["ollama_version"].stdout)),
            "installed_models": _observation(results["ollama_list"], installed_models),
            "loaded_models": len(parse_ollama_ps(next((r.stdout for r in reversed(resource_results) if r.name == "resource_ollama_ps"), ""))),
        },
        "machine": _observation(results["hardware"], parse_hardware(results["hardware"].stdout)),
        "storage": _observation(results["storage"], parse_df(results["storage"].stdout)),
        "resource_observations": {
            "samples": list(samples),
            "reasons": resource_verdict["reasons"],
            "idle_baseline": resource_verdict["idle_baseline"],
            "vm": _observation(results["vm_stat"], parse_vm_stat(results["vm_stat"].stdout)),
        },
        "luna_observation": {
            "status": "available" if samples else "not_observed",
            "active_process_count": samples[-1].get("luna_count") if samples else None,
            "aggregate": process_latest.get("luna") if isinstance(process_latest, dict) else None,
        },
        "schedulers": {
            "hermes_jobs": _observation(results["hermes_cron"], parse_hermes_cron(results["hermes_cron"].stdout)),
            "user_crontab": {"status": results["crontab"].status, "configured": bool(results["crontab"].stdout.strip()) if results["crontab"].status == "available" else None},
            "launchd": _observation(launchd_result, _launchd_labels(launchd_result.stdout)),
        },
        "browser_automation": {
            "status": results["hermes_computer_use"].status,
            "hermes_computer_use": results["hermes_computer_use"].stdout.strip()[:1000] if results["hermes_computer_use"].status == "available" else None,
            "dedicated_profile": {"status": "not_observed", "value": None},
        },
        "isolation": (
            dict(isolation_verdict)
            if isolation_verdict is not None
            else {"status": "not_observed", "value": None}
        ),
        "unknowns": unknowns,
        "warnings": list(resource_verdict["reasons"]),
        "source_commands": [_source_record(result) for result in [*results.values(), *resource_results]],
        "required_sections_blocked": required_blocked,
    }


def collect_inventory(
    context: InventoryContext,
    runner: Runner = run_command,
    sample_interval: float = 1.0,
    isolation_verdict: Mapping[str, object] | None = None,
) -> dict[str, object]:
    results = {spec.name: runner(spec) for spec in BASE_COMMANDS}
    model_rows = parse_ollama_list(results["ollama_list"].stdout)
    model_details: list[Mapping[str, object]] = []
    for index, row in enumerate(model_rows):
        detail_result = runner(
            CommandSpec(f"ollama_show_{index}", (_OLLAMA, "show", "-v", row["name"]))
        )
        results[detail_result.name] = detail_result
        model_details.append(parse_ollama_show(detail_result.stdout))
    samples, resource_results = collect_resource_samples(runner, 3, sample_interval)
    return build_inventory_document(
        context,
        results,
        model_rows,
        model_details,
        samples,
        resource_results,
        isolation_verdict,
    )
