#!/usr/bin/env python3
"""Measure Codex reasoning effort and concurrency on the HRL strict corpus."""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from hermes_revenue_lab.models.corpus import CORPUS_VERSION, benchmark_corpus, corpus_digest
from hermes_revenue_lab.models.validators import evaluate_response


STRICT_FAMILIES = {
    "analyze_business",
    "repair_collector",
    "synthesize_sources",
    "structured_audit",
}
MODELS = ("gpt-5.6-luna", "gpt-5.5")
EFFORTS = ("low", "medium", "high")
CONCURRENCY_LEVELS = (1, 2, 4)
LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_provider_module() -> Any:
    path = Path(__file__).with_name("run_cloud_provider_benchmarks.py")
    spec = importlib.util.spec_from_file_location("hrl_provider_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def invoke(
    client: OpenAI,
    helper: Any,
    model: str,
    effort: str,
    task: Any,
    phase: str,
    concurrency: int | None = None,
    repetition: int = 0,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "instructions": helper.SYSTEM,
            "input": [{"role": "user", "content": task.prompt}],
            "store": False,
            "reasoning": {"effort": effort},
        }
        tools = helper._tool_payload(task)
        if tools:
            kwargs.update(
                {
                    "tools": helper._responses_tools(tools),
                    "tool_choice": "auto",
                    "parallel_tool_calls": False,
                }
            )
        else:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "hrl_benchmark",
                    "strict": False,
                    "schema": task.output_schema,
                }
            }
        events = list(client.responses.create(**kwargs, stream=True))
        response = helper._consume_codex_event_stream(events, model=model)
        text_parts: list[str] = []
        tool_call = None
        for item in getattr(response, "output", ()) or ():
            item_type = getattr(item, "type", None)
            if item_type == "function_call" and tool_call is None:
                arguments = getattr(item, "arguments", "{}")
                try:
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except json.JSONDecodeError:
                    arguments = {}
                tool_call = {"name": getattr(item, "name", ""), "arguments": arguments}
            elif item_type == "message":
                for part in getattr(item, "content", ()) or ():
                    text = getattr(part, "text", None)
                    if isinstance(text, str):
                        text_parts.append(text)
        evaluation = evaluate_response(task, "".join(text_parts), tool_call=tool_call)
        return {
            "phase": phase,
            "model": model,
            "effort": effort,
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
            "phase": phase,
            "model": model,
            "effort": effort,
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


def summarize(records: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if row["phase"] != phase:
            continue
        key = (row["model"], row["effort"])
        if phase == "concurrency":
            key += (row["concurrency"],)
        grouped[key].append(row)
    result: dict[str, Any] = {}
    for key, rows in sorted(grouped.items()):
        latencies = [float(row["wall_time_seconds"]) for row in rows]
        label = "/".join(str(value) for value in key)
        result[label] = {
            "passes": sum(row["success"] is True for row in rows),
            "total": len(rows),
            "median_seconds": round(statistics.median(latencies), 6),
            "p95_seconds": round(percentile(latencies, 0.95), 6),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--model",
        action="append",
        help="Codex model to test; repeat for multiple models, including new candidates",
    )
    parser.add_argument("--effort", action="append", choices=EFFORTS)
    parser.add_argument("--concurrency", action="append", type=int, choices=CONCURRENCY_LEVELS)
    args = parser.parse_args()
    models = tuple(args.model or MODELS)
    efforts = tuple(args.effort or EFFORTS)
    concurrency_levels = tuple(args.concurrency or CONCURRENCY_LEVELS)
    helper = load_provider_module()
    spec = helper.provider_specs()["openai-codex"]
    client = OpenAI(
        api_key=spec["api_key"],
        base_url=spec["base_url"],
        default_headers=spec["headers"],
        max_retries=0,
        timeout=120.0,
    )
    tasks = tuple(task for task in benchmark_corpus() if task.family in STRICT_FAMILIES)
    document: dict[str, Any] = {
        "schema_version": "hrl.effort_concurrency.v1",
        "classification": "diagnostic-only",
        "corpus_version": CORPUS_VERSION,
        "corpus_sha256": corpus_digest(benchmark_corpus()),
        "started_at": now(),
        "status": "running",
        "models": list(models),
        "efforts": list(efforts),
        "concurrency_levels": list(concurrency_levels),
        "records": [],
    }
    persist(args.output, document)
    for model in models:
        for effort in efforts:
            for task in tasks:
                row = invoke(client, helper, model, effort, task, "effort")
                document["records"].append(row)
                persist(args.output, document)
                print(json.dumps(row), flush=True)
    for model in models:
        for effort in efforts:
            passed_task_ids = {
                row["task_id"]
                for row in document["records"]
                if row["phase"] == "effort"
                and row["model"] == model
                and row["effort"] == effort
                and row.get("success") is True
            }
            if passed_task_ids != {task.task_id for task in tasks}:
                continue
            for concurrency in concurrency_levels:
                workload = [(task, repetition) for repetition in range(2) for task in tasks]
                batch_started = time.monotonic()
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures = [
                        pool.submit(
                            invoke,
                            client,
                            helper,
                            model,
                            effort,
                            task,
                            "concurrency",
                            concurrency,
                            repetition,
                        )
                        for task, repetition in workload
                    ]
                    rows = [future.result() for future in futures]
                batch_seconds = time.monotonic() - batch_started
                for row in rows:
                    row["batch_wall_time_seconds"] = round(batch_seconds, 6)
                    row["throughput_per_second"] = round(len(rows) / batch_seconds, 6)
                    document["records"].append(row)
                    print(json.dumps(row), flush=True)
                persist(args.output, document)
    document["effort_summary"] = summarize(document["records"], "effort")
    document["concurrency_summary"] = summarize(document["records"], "concurrency")
    document["status"] = "completed"
    document["ended_at"] = now()
    persist(args.output, document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
