"""Authoritative catalog of work that is structurally ineligible for an LLM."""

from __future__ import annotations


DETERMINISTIC_OPERATIONS = frozenset(
    {
        "url_change",
        "document_hash",
        "timestamp_compare",
        "exact_id_deduplicate",
        "decimal_arithmetic",
        "sqlite_query",
        "experiment_metrics",
        "revenue_calculation",
        "health_endpoint",
        "cpu_load",
        "ram_inspection",
        "market_schedule",
        "threshold_compare",
    }
)


def require_no_llm(operation: str, requested_tier: str) -> str:
    if operation not in DETERMINISTIC_OPERATIONS:
        raise ValueError(f"unknown deterministic operation {operation}")
    if requested_tier != "no_llm":
        raise ValueError(f"deterministic operation {operation} requires no_llm")
    return "no_llm"
