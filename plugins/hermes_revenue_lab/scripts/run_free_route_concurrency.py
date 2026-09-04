#!/usr/bin/env python3
"""Measure concurrency for free cloud routes qualified by HRL v3 correctness."""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from hermes_revenue_lab.models.corpus import CORPUS_VERSION, benchmark_corpus, corpus_digest
from hermes_revenue_lab.models.validators import evaluate_response


STRICT_FAMILIES = {"analyze_business", "repair_collector", "synthesize_sources", "structured_audit"}
QUALIFIED = (
    ("nous", "tencent/hy3:free"),
    ("openrouter", "thinkingmachines/inkling-small:free"),
)
CONCURRENCY_LEVELS = (1, 2, 4)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_helper() -> Any:
    path = Path(__file__).with_name("run_cloud_provider_benchmarks.py")
    spec = importlib.util.spec_from_file_location("hrl_provider_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke(client: OpenAI, helper: Any, provider: str, model: str, task: Any, concurrency: int, repetition: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        text, tool_call = helper.run_chat(client, model, task)
        evaluation = evaluate_response(task, text, tool_call=tool_call)
        return {
            "provider": provider,
            "model": model,
            "task_id": task.task_id,
            "task_family": task.family,
            "concurrency": concurrency,
            "repetition": repetition,
            "status": "completed",
            "success": evaluation.success,
            "reason_codes": list(evaluation.reason_codes),
            "wall_time_seconds": round(time.monotonic() - started, 6),
            "ended_at": now(),
        }
    except Exception as exc:
        return {
            "provider": provider,
            "model": model,
            "task_id": task.task_id,
            "task_family": task.family,
            "concurrency": concurrency,
            "repetition": repetition,
            "status": "failed",
            "success": False,
            "reason_codes": [f"transport_{type(exc).__name__}"],
            "error": str(exc)[:500],
            "wall_time_seconds": round(time.monotonic() - started, 6),
            "ended_at": now(),
        }


def persist(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[(row["provider"], row["model"], row["concurrency"])].append(row)
    result: dict[str, Any] = {}
    for (provider, model, concurrency), rows in sorted(grouped.items()):
        latencies = sorted(float(row["wall_time_seconds"]) for row in rows)
        result[f"{provider}/{model}/{concurrency}"] = {
            "passes": sum(row["success"] is True for row in rows),
            "total": len(rows),
            "median_seconds": round(statistics.median(latencies), 6),
            "p95_seconds": round(latencies[max(0, round((len(latencies) - 1) * 0.95))], 6),
            "batch_wall_time_seconds": rows[0]["batch_wall_time_seconds"],
            "throughput_per_second": rows[0]["throughput_per_second"],
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--route",
        action="append",
        metavar="PROVIDER=MODEL",
        help="Qualified route to test; repeat for multiple routes",
    )
    parser.add_argument("--concurrency", action="append", type=int, choices=CONCURRENCY_LEVELS)
    args = parser.parse_args()
    qualified: list[tuple[str, str]] = []
    for value in args.route or ():
        provider, separator, model = value.partition("=")
        if not separator or not provider or not model:
            parser.error(f"invalid --route {value!r}; expected PROVIDER=MODEL")
        qualified.append((provider, model))
    routes = tuple(qualified or QUALIFIED)
    concurrency_levels = tuple(args.concurrency or CONCURRENCY_LEVELS)
    helper = load_helper()
    specs = helper.provider_specs()
    tasks = tuple(task for task in benchmark_corpus() if task.family in STRICT_FAMILIES)
    document: dict[str, Any] = {
        "schema_version": "hrl.free_concurrency.v1",
        "classification": "diagnostic-only",
        "corpus_version": CORPUS_VERSION,
        "corpus_sha256": corpus_digest(benchmark_corpus()),
        "qualified_routes": [{"provider": provider, "model": model} for provider, model in routes],
        "concurrency_levels": list(concurrency_levels),
        "started_at": now(),
        "status": "running",
        "records": [],
    }
    persist(args.output, document)
    for provider, model in routes:
        if provider not in specs:
            parser.error(f"unknown provider in --route: {provider}")
        spec = specs[provider]
        client = OpenAI(
            api_key=spec["api_key"],
            base_url=spec["base_url"],
            default_headers=spec["headers"],
            max_retries=0,
            timeout=120.0,
        )
        for concurrency in concurrency_levels:
            workload = [(task, repetition) for repetition in range(2) for task in tasks]
            batch_started = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                rows = list(
                    pool.map(
                        lambda pair: invoke(client, helper, provider, model, pair[0], concurrency, pair[1]),
                        workload,
                    )
                )
            batch_seconds = time.monotonic() - batch_started
            for row in rows:
                row["batch_wall_time_seconds"] = round(batch_seconds, 6)
                row["throughput_per_second"] = round(len(rows) / batch_seconds, 6)
                document["records"].append(row)
                print(json.dumps(row), flush=True)
            persist(args.output, document)
    document["summary"] = summarize(document["records"])
    document["status"] = "completed"
    document["ended_at"] = now()
    persist(args.output, document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
