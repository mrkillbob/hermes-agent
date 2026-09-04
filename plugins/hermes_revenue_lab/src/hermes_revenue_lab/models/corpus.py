"""Fixed synthetic benchmark corpus for HRL-1."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from .types import BenchmarkTask


CORPUS_VERSION = "hrl.benchmark.v3"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _task(
    task_id: str,
    family: str,
    benchmark_tier: str,
    production_tier: str,
    fixture_count: int,
    instruction: str,
    fixtures: object,
    expected: object,
    schema: object,
    *,
    thinking_allowed: bool = False,
    tools: object | None = None,
) -> BenchmarkTask:
    response_instruction = (
        "Emit exactly one declared tool call. The harness will inspect it without executing it."
        if tools is not None
        else "Return only JSON matching the supplied schema."
    )
    prompt = (
        f"Synthetic local benchmark {task_id}. {instruction}\n"
        f"Input: {_canonical(fixtures)}\n"
        f"{response_instruction}"
    )
    return BenchmarkTask(
        task_id=task_id,
        family=family,
        benchmark_tier=benchmark_tier,  # type: ignore[arg-type]
        production_tier=production_tier,  # type: ignore[arg-type]
        fixture_count=fixture_count,
        prompt=prompt,
        expected_json=_canonical(expected),
        output_schema_json=_canonical(schema),
        thinking_allowed=thinking_allowed,
        tool_schema_json=_canonical(tools) if tools is not None else None,
    )


def _classification_task() -> BenchmarkTask:
    categories = ("audit", "data", "alert", "digital_product", "reject")
    records = []
    expected = []
    for index in range(20):
        category = categories[index % len(categories)]
        records.append(
            {
                "id": f"O{index + 1:02d}",
                "evidence": {
                    "audit": "public contact form returns a reproducible validation error",
                    "data": "public permit rows require repeated normalization",
                    "alert": "authoritative notice feed changes unpredictably",
                    "digital_product": "buyers repeatedly request a pricing calculator",
                    "reject": "claim has no cited observation",
                }[category],
            }
        )
        expected.append({"id": f"O{index + 1:02d}", "category": category})
    return _task(
        "classify-20-v1",
        "classify_opportunities",
        "fast",
        "fast",
        20,
        (
            "Use this exact codebook: audit = reproducible site defect; data = repeated "
            "normalization; alert = changing authoritative feed; digital_product = repeated "
            "buyer request; reject = no cited observation. Classify every evidence record."
        ),
        records,
        {"classifications": expected},
        {
            "type": "object",
            "required": ["classifications"],
            "properties": {
                "classifications": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "category"],
                        "properties": {
                            "id": {"type": "string"},
                            "category": {"type": "string", "enum": list(categories)},
                        },
                        "additionalProperties": False,
                    },
                }
            },
            "additionalProperties": False,
        },
    )


def _extraction_task() -> BenchmarkTask:
    pages = []
    rows = []
    for index in range(10):
        page_id = f"P{index + 1:02d}"
        pages.append(
            {
                "page_id": page_id,
                "text": (
                    f"Vendor {index + 1}; public plan ${20 + index}/month; "
                    f"updated 2026-08-{index + 1:02d}; support: email."
                ),
            }
        )
        rows.append(
            {
                "page_id": page_id,
                "vendor": f"Vendor {index + 1}",
                "monthly_price": 20 + index,
                "updated": f"2026-08-{index + 1:02d}",
            }
        )
    return _task(
        "extract-10-v1",
        "extract_pages",
        "fast",
        "fast",
        10,
        "Extract only the stated vendor, monthly price as an integer, and update date.",
        pages,
        {"rows": rows},
        {
            "type": "object",
            "required": ["rows"],
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["page_id", "vendor", "monthly_price", "updated"],
                        "properties": {
                            "page_id": {"type": "string"},
                            "vendor": {"type": "string"},
                            "monthly_price": {"type": "integer"},
                            "updated": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                }
            },
            "additionalProperties": False,
        },
    )


def _deduplication_task() -> BenchmarkTask:
    records = []
    unique_ids = []
    for index in range(50):
        record_id = f"R{index + 1:03d}"
        unique_ids.append(record_id)
        records.extend(
            (
                {"record_id": record_id, "source_row": index * 2 + 1},
                {"record_id": record_id, "source_row": index * 2 + 2},
            )
        )
    return _task(
        "deduplicate-100-v1",
        "deduplicate_records",
        "fast",
        "no_llm",
        100,
        "Return each exact record_id once in ascending order. Production performs this without an LLM.",
        records,
        {"unique_record_ids": unique_ids},
        {
            "type": "object",
            "required": ["unique_record_ids"],
            "properties": {"unique_record_ids": {"type": "array"}},
        },
    )


def benchmark_corpus() -> tuple[BenchmarkTask, ...]:
    tasks = (
        _classification_task(),
        _extraction_task(),
        _deduplication_task(),
        _task(
            "analyze-business-v1",
            "analyze_business",
            "standard",
            "standard",
            1,
            (
                "Classify only supported evidence. Use problem code booking_validation for a "
                "booking submit/validation failure and hours_conflict for conflicting official "
                "hours. Put direct problem evidence in evidence_ids and competitor-only context "
                "in competitor_evidence_ids. Preserve the business name exactly."
            ),
            {
                "business": "Northwind Repair",
                "evidence": [
                    {"id": "E1", "fact": "mobile booking form submit returns status 422"},
                    {"id": "E2", "fact": "posted hours differ across two official pages"},
                    {"id": "E3", "fact": "competitor publishes same-day availability"},
                ],
            },
            {
                "business": "Northwind Repair",
                "problem_codes": ["booking_validation", "hours_conflict"],
                "evidence_ids": ["E1", "E2"],
                "competitor_evidence_ids": ["E3"],
            },
            {
                "type": "object",
                "required": ["business", "problem_codes", "evidence_ids", "competitor_evidence_ids"],
                "properties": {
                    "business": {"type": "string"},
                    "problem_codes": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["booking_validation", "hours_conflict"],
                        },
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "competitor_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "additionalProperties": False,
            },
        ),
        _task(
            "score-opportunity-v1",
            "score_opportunity",
            "standard",
            "standard",
            1,
            (
                "Score integer components with this exact rubric: high = 5, medium = 3, "
                "low = 1. Preserve the cited evidence identifiers in input order."
            ),
            {
                "evidence": [
                    {"id": "D1", "dimension": "demand", "level": "high"},
                    {"id": "A1", "dimension": "automation", "level": "medium"},
                    {"id": "R1", "dimension": "policy_risk", "level": "low"},
                ]
            },
            {"demand": 5, "automation": 3, "policy_risk": 1, "evidence_ids": ["D1", "A1", "R1"]},
            {
                "type": "object",
                "required": ["demand", "automation", "policy_risk", "evidence_ids"],
                "properties": {
                    "demand": {"type": "integer", "minimum": 1, "maximum": 5},
                    "automation": {"type": "integer", "minimum": 1, "maximum": 5},
                    "policy_risk": {"type": "integer", "minimum": 1, "maximum": 5},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        ),
        _task(
            "select-tool-v1",
            "select_tool",
            "fast",
            "fast",
            1,
            "Choose the one declared tool that stores a normalized candidate. Do not execute anything.",
            {"candidate_id": "C-17", "title": "Permit alert", "score": 4},
            {"tool": "store_candidate", "arguments": {"candidate_id": "C-17", "score": 4}},
            {"type": "object", "required": ["tool", "arguments"]},
            tools=[
                {"name": "store_candidate", "required": ["candidate_id", "score"]},
                {"name": "delete_candidate", "required": ["candidate_id"]},
            ],
        ),
        _task(
            "synthesize-sources-v1",
            "synthesize_sources",
            "standard",
            "standard",
            3,
            (
                "Use conclusion_code recurring_alert_candidate when an often-changing feed, "
                "manual subscriber checking, and permission for derived alerts are all "
                "supported. Cite every source identifier used."
            ),
            [
                {"id": "S1", "text": "The public feed updates every weekday."},
                {"id": "S2", "text": "Subscribers report checking changes manually."},
                {"id": "S3", "text": "The feed license permits redistribution of derived alerts."},
            ],
            {"conclusion_code": "recurring_alert_candidate", "source_ids": ["S1", "S2", "S3"]},
            {
                "type": "object",
                "required": ["conclusion_code", "source_ids"],
                "properties": {
                    "conclusion_code": {
                        "type": "string",
                        "enum": ["recurring_alert_candidate"],
                    },
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        ),
        _task(
            "repair-collector-v1",
            "repair_collector",
            "coding",
            "coding",
            1,
            (
                "Return Python source for parse_prices(text) that skips malformed rows and "
                "returns integer prices. Do not use try/except, imports, or exception raising. "
                "Split each row on commas, require a second field, and use str.isdigit() before "
                "converting it with int()."
            ),
            {"broken": "def parse_prices(text): return [line.split(',')[1] for line in text.splitlines()]"},
            {
                "function": "parse_prices",
                "example_input": "a,10\\nb,bad\\nc,30\\nmissing",
                "example_output": [10, 30],
            },
            {
                "type": "object",
                "required": ["python", "function"],
                "properties": {
                    "python": {"type": "string"},
                    "function": {"type": "string", "enum": ["parse_prices"]},
                },
                "additionalProperties": False,
            },
        ),
        _task(
            "structured-audit-v1",
            "structured_audit",
            "standard",
            "standard",
            1,
            "Create an evidence-only audit with no unsupported claim.",
            {
                "evidence": [
                    {"id": "A1", "problem": "broken_link", "location": "pricing page"},
                    {"id": "A2", "problem": "missing_alt_text", "location": "product image"},
                ]
            },
            {"finding_codes": ["broken_link", "missing_alt_text"], "evidence_ids": ["A1", "A2"]},
            {
                "type": "object",
                "required": ["finding_codes", "evidence_ids", "remedies"],
                "properties": {
                    "finding_codes": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["broken_link", "missing_alt_text"],
                        },
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "remedies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                },
                "additionalProperties": False,
            },
        ),
        _task(
            "decide-escalation-v1",
            "decide_escalation",
            "fast",
            "fast",
            1,
            (
                "Decide whether conflicting policy evidence requires escalation. Use only these "
                "exact reason codes when applicable: policy_conflict = permission and prohibition "
                "conflict; when intended_action contains \"publish\", include publication_action. "
                "Include every applicable reason code. Set escalate to true whenever any reason "
                "code applies, and false only when none apply."
            ),
            {
                "fast_result": "automation allowed",
                "source_1": "API automation allowed within rate limits",
                "source_2": "automated listing publication prohibited",
                "intended_action": "publish listings automatically",
            },
            {"escalate": True, "reason_codes": ["policy_conflict", "publication_action"]},
            {
                "type": "object",
                "required": ["escalate", "reason_codes"],
                "properties": {
                    "escalate": {"type": "boolean"},
                    "reason_codes": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        ),
    )
    return tasks


def corpus_digest(tasks: Sequence[BenchmarkTask]) -> str:
    payload = {
        "corpus_version": CORPUS_VERSION,
        "tasks": [task.canonical_record() for task in tasks],
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
