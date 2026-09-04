"""Inventory-bound orchestration for HRL-1 model measurements."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .benchmark_guard import GuardDecision
from .corpus import CORPUS_VERSION, benchmark_corpus, corpus_digest
from .ollama_client import run_ollama_task
from .selection import select_models
from .types import BenchmarkTask, ModelCandidate, OllamaTaskResponse
from .validators import evaluate_response


DecisionProvider = Callable[[ModelCandidate], GuardDecision]
Transport = Callable[[str, BenchmarkTask], OllamaTaskResponse]
ResourceMeasurer = Callable[
    [Callable[[], OllamaTaskResponse]], tuple[OllamaTaskResponse, Mapping[str, object]]
]
CandidateReleaser = Callable[[ModelCandidate], bool]


def _parameter_billions(text: str) -> float:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)B\s*", text, re.I)
    if not match:
        raise ValueError(f"unsupported parameter label: {text!r}")
    return float(match.group(1))


def load_inventory_candidates(inventory: Mapping[str, object]) -> tuple[ModelCandidate, ...]:
    inventory_id = str(inventory["inventory_id"])
    ollama = inventory.get("ollama")
    if not isinstance(ollama, dict):
        raise ValueError("inventory has no Ollama evidence")
    installed = ollama.get("installed_models")
    if not isinstance(installed, dict) or installed.get("status") != "available":
        raise ValueError("installed Ollama model evidence is unavailable")
    rows = installed.get("value")
    if not isinstance(rows, list):
        raise ValueError("installed Ollama model rows are unavailable")
    candidates: list[ModelCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parameters = str(row.get("parameters", ""))
        candidates.append(
            ModelCandidate(
                name=str(row["name"]),
                digest=str(row["digest"]),
                parameters=parameters,
                parameter_billions=_parameter_billions(parameters),
                quantization=str(row.get("quantization", "unavailable")),
                size=str(row.get("size", "unavailable")),
                capabilities=tuple(sorted(str(value) for value in row.get("capabilities", []))),
                inventory_id=inventory_id,
            )
        )
    return tuple(candidates)


def candidate_roles(candidates: Sequence[ModelCandidate]) -> dict[str, tuple[str, ...]]:
    roles: dict[str, tuple[str, ...]] = {}
    for candidate in candidates:
        assigned: list[str] = []
        name = candidate.name.lower()
        billions = candidate.parameter_billions
        tool_capable = "tools" in candidate.capabilities
        thinking_capable = "thinking" in candidate.capabilities
        if tool_capable and 3.0 <= billions <= 6.0:
            assigned.append("fast")
        if tool_capable and 7.0 <= billions <= 12.0:
            assigned.append("standard")
        if tool_capable and thinking_capable and name.startswith("gpt-oss:") and 15.0 <= billions <= 25.0:
            assigned.append("reasoning")
        if tool_capable and name.startswith("qwen3-coder:") and 25.0 <= billions <= 35.0:
            assigned.append("coding")
        if (
            tool_capable
            and thinking_capable
            and (name.startswith("qwen3.5:") or name.startswith("qwen3.6:"))
            and 25.0 <= billions <= 30.0
        ):
            assigned.append("escalation")
        roles[candidate.name] = tuple(assigned)
    return roles


def _timestamp(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def benchmark_candidate(
    candidate: ModelCandidate,
    tasks: Sequence[BenchmarkTask],
    *,
    decision_provider: DecisionProvider,
    transport: Transport = run_ollama_task,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    role: str | None = None,
    resource_measurer: ResourceMeasurer | None = None,
) -> list[dict[str, object]]:
    """Benchmark tasks sequentially, refreshing the guard before each request."""

    records: list[dict[str, object]] = []
    inferred_roles = candidate_roles((candidate,))[candidate.name]
    record_role = role or (inferred_roles[0] if inferred_roles else "unassigned")
    for task in tasks:
        started_at = _timestamp(now)
        decision = decision_provider(candidate)
        base: dict[str, object] = {
            "model": candidate.name,
            "model_digest": candidate.digest,
            "parameters": candidate.parameters,
            "quantization": candidate.quantization,
            "inventory_id": candidate.inventory_id,
            "task_id": task.task_id,
            "task_family": task.family,
            "role": record_role,
            "started_at": started_at,
            "guard_state": decision.state,
            "guard_reason_codes": list(decision.reasons),
            "retries": 0,
            "estimated_compute_cost": None,
        }
        if not decision.permitted:
            records.append(
                {
                    **base,
                    "status": "blocked",
                    "success": False,
                    "ended_at": _timestamp(now),
                    "reason_codes": list(decision.reasons),
                }
            )
            continue
        try:
            if resource_measurer is None:
                response = transport(candidate.name, task)
                resource_metrics: Mapping[str, object] = {
                    "peak_ollama_rss_bytes": None,
                    "peak_ollama_cpu_percent": None,
                    "gpu_pressure": None,
                    "resource_measurement_reason": "bounded sampler was not supplied",
                }
            else:
                response, resource_metrics = resource_measurer(
                    lambda: transport(candidate.name, task)
                )
            evaluation = evaluate_response(
                task,
                response.response_text,
                tool_call=response.tool_call,
            )
            response_digest = hashlib.sha256(response.response_text.encode("utf-8")).hexdigest()
            records.append(
                {
                    **base,
                    "status": "completed",
                    **dict(resource_metrics),
                    "success": evaluation.success,
                    "structured_valid": evaluation.structured_valid,
                    "correctness": evaluation.correctness,
                    "tool_call_correct": evaluation.tool_call_correct,
                    "unnecessary_thinking": evaluation.unnecessary_thinking,
                    "reason_codes": list(evaluation.reason_codes),
                    "response_sha256": response_digest,
                    "wall_time_seconds": response.wall_time_seconds,
                    "time_to_first_token_seconds": response.time_to_first_token_seconds,
                    "prompt_eval_count": response.prompt_eval_count,
                    "eval_count": response.eval_count,
                    "tokens_per_second": response.tokens_per_second,
                    "load_duration_seconds": response.load_duration_seconds,
                    "prompt_eval_duration_seconds": response.prompt_eval_duration_seconds,
                    "eval_duration_seconds": response.eval_duration_seconds,
                    "total_duration_seconds": response.total_duration_seconds,
                    "ended_at": _timestamp(now),
                }
            )
        except Exception as exc:
            records.append(
                {
                    **base,
                    "status": "failed",
                    "success": False,
                    "ended_at": _timestamp(now),
                    "reason_codes": [f"transport_{type(exc).__name__}"],
                }
            )
    return records


ROLE_FAMILIES = {
    "fast": (
        "classify_opportunities",
        "extract_pages",
        "deduplicate_records",
        "select_tool",
        "decide_escalation",
    ),
    "standard": (
        "analyze_business",
        "score_opportunity",
        "synthesize_sources",
        "structured_audit",
    ),
    "reasoning": (
        "analyze_business",
        "score_opportunity",
        "synthesize_sources",
        "decide_escalation",
    ),
    "coding": ("repair_collector",),
    "escalation": (
        "analyze_business",
        "score_opportunity",
        "synthesize_sources",
        "structured_audit",
    ),
}


def run_benchmark_suite(
    inventory: Mapping[str, object],
    *,
    requested_roles: Sequence[str],
    decision_provider: DecisionProvider,
    transport: Transport = run_ollama_task,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    resource_measurer: ResourceMeasurer | None = None,
    candidate_releaser: CandidateReleaser | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    unknown_roles = sorted(set(requested_roles) - set(ROLE_FAMILIES))
    if unknown_roles:
        raise ValueError(f"unknown benchmark roles: {', '.join(unknown_roles)}")
    started_at = _timestamp(now)
    candidates = load_inventory_candidates(inventory)
    roles = candidate_roles(candidates)
    tasks = benchmark_corpus()
    by_family = {task.family: task for task in tasks}
    records: list[dict[str, object]] = []
    cleanup_events: list[dict[str, object]] = []
    cleanup_failed = False
    for role in requested_roles:
        if cleanup_failed:
            break
        role_tasks = [by_family[family] for family in ROLE_FAMILIES[role]]
        for candidate in candidates:
            if role not in roles[candidate.name]:
                continue
            candidate_records = benchmark_candidate(
                candidate,
                role_tasks,
                decision_provider=decision_provider,
                transport=transport,
                now=now,
                role=role,
                resource_measurer=resource_measurer,
            )
            records.extend(candidate_records)
            if candidate_releaser is not None and any(
                record["status"] != "blocked" for record in candidate_records
            ):
                released = candidate_releaser(candidate)
                cleanup_events.append(
                    {
                        "model": candidate.name,
                        "model_digest": candidate.digest,
                        "status": "released" if released else "failed",
                    }
                )
                if not released:
                    cleanup_failed = True
                    break
    if cleanup_failed:
        status = "partial"
    elif records and all(record["status"] == "blocked" for record in records):
        status = "blocked"
    elif records and all(record["status"] == "completed" for record in records):
        status = "completed"
    elif records:
        status = "partial"
    else:
        status = "unavailable"
    benchmark_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    document: dict[str, object] = {
        "schema_version": "hrl.model_benchmark.v1",
        "benchmark_id": benchmark_id,
        "inventory_id": str(inventory["inventory_id"]),
        "corpus_version": CORPUS_VERSION,
        "corpus_sha256": corpus_digest(tasks),
        "started_at": started_at,
        "ended_at": _timestamp(now),
        "status": status,
        "requested_roles": list(requested_roles),
        "candidate_registry": [
            {
                "name": candidate.name,
                "digest": candidate.digest,
                "parameters": candidate.parameters,
                "quantization": candidate.quantization,
                "size": candidate.size,
                "capabilities": list(candidate.capabilities),
                "roles": list(roles[candidate.name]),
            }
            for candidate in candidates
        ],
        "excluded_candidates": [
            {
                "name": candidate.name,
                "digest": candidate.digest,
                "reason": "no automatic benchmark role matches the approved candidate class",
            }
            for candidate in candidates
            if not roles[candidate.name]
        ],
        "records": records,
        "cleanup_events": cleanup_events,
        "unknowns": (
            ["resource deltas were not observed because no inference started"]
            if not any(record.get("status") == "completed" for record in records)
            else ["GPU pressure remains unavailable because no trusted sampler is configured"]
        ),
    }
    selections = select_models(
        candidates,
        roles,
        records,
        inventory_id=str(inventory["inventory_id"]),
    )
    return document, selections
