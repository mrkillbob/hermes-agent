"""Deterministic resource-aware selection from completed benchmark records."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence

from .types import ModelCandidate


REQUIRED_TASK_COUNTS = {
    "fast": 5,
    "standard": 4,
    "reasoning": 4,
    "coding": 1,
    "escalation": 4,
}


def _candidate_summary(
    candidate: ModelCandidate,
    role: str,
    records: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    rows = [
        row for row in records if row.get("model") == candidate.name and row.get("role") == role
    ]
    required = REQUIRED_TASK_COUNTS[role]
    completed = [row for row in rows if row.get("status") == "completed"]
    if len(completed) < required:
        return None
    scoped = completed[:required]
    if not all(row.get("success") is True and row.get("structured_valid") is True for row in scoped):
        return None
    walls = [float(row["wall_time_seconds"]) for row in scoped]
    peak_rss_values = [
        int(value)
        for row in scoped
        if isinstance((value := row.get("peak_ollama_rss_bytes")), (int, float))
        and not isinstance(value, bool)
        and value > 0
    ]
    if len(peak_rss_values) != required:
        return None
    correctness = [
        float(row["correctness"]) for row in scoped if isinstance(row.get("correctness"), (int, float))
    ]
    return {
        "model": candidate.name,
        "model_digest": candidate.digest,
        "parameter_billions": candidate.parameter_billions,
        "peak_ollama_rss_bytes": max(peak_rss_values),
        "median_wall_time_seconds": round(statistics.median(walls), 6),
        "quality_score": round(statistics.mean(correctness), 6) if correctness else None,
        "completed_tasks": len(scoped),
    }


def _unavailable(reason: str) -> dict[str, object]:
    return {"status": "unavailable", "model": None, "reason": reason}


def select_models(
    candidates: Sequence[ModelCandidate],
    roles: Mapping[str, tuple[str, ...]],
    records: Sequence[Mapping[str, object]],
    *,
    inventory_id: str,
    tier4_materiality: float = 0.10,
) -> dict[str, object]:
    tiers: dict[str, dict[str, object]] = {
        "no_llm": {"status": "available", "model": None, "reason": "deterministic execution"}
    }
    summaries_by_role: dict[str, list[dict[str, object]]] = {}
    for role in REQUIRED_TASK_COUNTS:
        eligible = [candidate for candidate in candidates if role in roles.get(candidate.name, ())]
        summaries = [
            summary
            for candidate in eligible
            if (summary := _candidate_summary(candidate, role, records)) is not None
        ]
        summaries.sort(
            key=lambda row: (
                int(row["peak_ollama_rss_bytes"]),
                float(row["median_wall_time_seconds"]),
                float(row["parameter_billions"]),
                str(row["model"]),
            )
        )
        summaries_by_role[role] = summaries
        if summaries:
            tiers[role] = {"status": "available", **summaries[0]}
        elif not eligible:
            tiers[role] = _unavailable("no installed candidate matches the required class")
        else:
            tiers[role] = _unavailable("benchmark evidence is incomplete or below threshold")

    escalation = tiers["escalation"]
    standard = tiers["standard"]
    if escalation["status"] == "available":
        if standard["status"] != "available":
            tiers["escalation"] = _unavailable("lower standard tier is unavailable")
        else:
            escalation_quality = escalation.get("quality_score")
            standard_quality = standard.get("quality_score")
            if not isinstance(escalation_quality, (int, float)) or not isinstance(
                standard_quality, (int, float)
            ):
                tiers["escalation"] = _unavailable("material quality improvement is unmeasured")
            elif float(escalation_quality) - float(standard_quality) < tier4_materiality:
                tiers["escalation"] = _unavailable("quality improvement is not material")

    return {
        "schema_version": "hrl.model_selections.v1",
        "inventory_id": inventory_id,
        "selection_rule": (
            "lowest measured peak RSS, then median wall time, then parameter count"
        ),
        "tiers": tiers,
    }
