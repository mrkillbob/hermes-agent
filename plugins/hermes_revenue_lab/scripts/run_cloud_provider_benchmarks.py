#!/usr/bin/env python3
"""Run the current HRL corpus against configured cloud providers.

Diagnostic-only: synthetic prompts, no tools are executed, no runtime or
broker process is started, and raw responses are retained only as hashes.
"""

# ruff: noqa: E402 -- verified Hermes and HRL roots are added before project imports.

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HRL_ROOT = Path(__file__).resolve().parents[1]
# HRL now lives in-tree at plugins/hermes_revenue_lab/, so the hermes-agent
# repo root is HRL_ROOT's great-grandparent. HERMES_AGENT_ROOT stays as an
# override for anyone still running this script against an out-of-tree
# hermes-agent checkout.
HERMES_ROOT = Path(
    os.environ.get("HERMES_AGENT_ROOT", HRL_ROOT.parents[1])
).expanduser()
sys.path[:0] = [str(HERMES_ROOT), str(HRL_ROOT / "src")]

from dotenv import load_dotenv
from openai import OpenAI

from agent.auxiliary_client import _codex_cloudflare_headers
from agent.codex_runtime import _consume_codex_event_stream
from agent.codex_responses_adapter import _responses_tools
from hermes_cli.auth import resolve_codex_runtime_credentials, resolve_nous_runtime_credentials
from hermes_revenue_lab.models.benchmark import ROLE_FAMILIES
from hermes_revenue_lab.models.corpus import CORPUS_VERSION, benchmark_corpus, corpus_digest
from hermes_revenue_lab.models.ollama_client import _tool_payload
from hermes_revenue_lab.models.validators import evaluate_response


OPENROUTER_FREE = (
    "thinkingmachines/inkling:free",
    "thinkingmachines/inkling-small:free",
    "minimax/minimax-m3:free",
    "z-ai/glm-5.2:free",
    "poolside/laguna-s-2.1:free",
    "poolside/laguna-xs-2.1:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3.5-lightning:free",
)
OPENCODE_FREE = (
    "deepseek-v4-flash-free",
    "muse-spark-1.2-contributor-free",
    "mimo-v2.5-free",
    "ling-3.0-flash-fin-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "laguna-s-2.1-free",
)
NOUS_FREE = (
    "upstage/solar-pro4:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "meituan/longcat-2.0:free",
    "poolside/laguna-s-2.1:free",
    "poolside/laguna-xs-2.1:free",
    "stepfun/step-3.7-flash:free",
    "tencent/hy3:free",
)
CODEX = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.3-codex-spark",
)

SYSTEM = "Follow the synthetic benchmark. Do not browse or call undeclared tools. Return only the requested result."
WRITE_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def provider_specs() -> dict[str, dict[str, Any]]:
    load_dotenv(Path.home() / ".hermes" / ".env", override=False)
    nous = resolve_nous_runtime_credentials()
    codex = resolve_codex_runtime_credentials()
    return {
        "openrouter": {
            "api_key": os.environ.get("OPENROUTER_API_KEY", ""),
            "base_url": "https://openrouter.ai/api/v1",
            "headers": {"HTTP-Referer": "https://hermes-agent.nousresearch.com", "X-Title": "Hermes Agent"},
            "models": OPENROUTER_FREE,
            "mode": "chat",
        },
        "opencode-free": {
            "api_key": "opencode-free-keyless",
            "base_url": "https://opencode.ai/zen/v1",
            "headers": {
                "Authorization": "",
                "HTTP-Referer": "https://hermes-agent.nousresearch.com",
                "X-Title": "Hermes Agent",
                "User-Agent": "HermesAgent/provider-benchmark",
            },
            "models": OPENCODE_FREE,
            "mode": "chat",
        },
        "nous": {
            "api_key": nous.get("api_key", ""),
            "base_url": nous.get("base_url", "https://inference-api.nousresearch.com/v1"),
            "headers": {},
            "models": NOUS_FREE,
            "mode": "chat",
        },
        "openai-codex": {
            "api_key": codex.get("api_key", ""),
            "base_url": codex.get("base_url", "https://chatgpt.com/backend-api/codex"),
            "headers": _codex_cloudflare_headers(
                codex.get("api_key", ""),
                base_url=codex.get("base_url", "https://chatgpt.com/backend-api/codex"),
            ),
            "models": CODEX,
            "mode": "responses",
        },
    }


def response_checksum(text: str, tool_call: dict[str, object] | None) -> str:
    payload = {"text": text, "tool_call": tool_call}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_tool(output: Any) -> dict[str, object] | None:
    calls = getattr(output, "tool_calls", None)
    if calls:
        function = calls[0].function
        arguments = function.arguments
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            return None
        return {"name": function.name, "arguments": arguments}
    return None


def run_chat(client: OpenAI, model: str, task: Any) -> tuple[str, dict[str, object] | None]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task.prompt}],
    }
    tools = _tool_payload(task)
    if tools:
        kwargs.update({"tools": tools, "tool_choice": "auto"})
    else:
        # Preserve HRL's constrained-output contract. A relay that cannot
        # carry the declared schema is not equivalent to the local benchmark
        # path and is recorded as a route failure rather than silently given
        # an easier prompt-only task.
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "hrl_benchmark", "strict": False, "schema": task.output_schema},
        }
    response = client.chat.completions.create(**kwargs)
    message = response.choices[0].message
    return message.content or "", normalize_tool(message)


def run_responses(client: OpenAI, model: str, task: Any) -> tuple[str, dict[str, object] | None]:
    kwargs: dict[str, Any] = {
        "model": model,
        "instructions": SYSTEM,
        "input": [{"role": "user", "content": task.prompt}],
        "store": False,
    }
    tools = _tool_payload(task)
    if tools:
        kwargs.update({"tools": _responses_tools(tools), "tool_choice": "auto", "parallel_tool_calls": False})
    else:
        kwargs["text"] = {
            "format": {"type": "json_schema", "name": "hrl_benchmark", "strict": False, "schema": task.output_schema}
        }
    event_stream = client.responses.create(**kwargs, stream=True)
    events = list(event_stream)
    response = _consume_codex_event_stream(events, model=model)
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
    return "".join(text_parts), tool_call


def retry_delay(error: Exception, attempt: int) -> float | None:
    status = getattr(error, "status_code", None)
    if status not in {408, 409, 429, 500, 502, 503, 504}:
        return None
    headers = getattr(error, "response", None)
    headers = getattr(headers, "headers", {}) if headers is not None else {}
    try:
        retry_after = float(headers.get("retry-after", 0))
    except (TypeError, ValueError):
        retry_after = 0
    return max(retry_after, min(15.0 * (2**attempt), 120.0))


def run_one(client: OpenAI, provider: str, spec: dict[str, Any], model: str, task: Any) -> dict[str, Any]:
    started = time.monotonic()
    error: Exception | None = None
    for attempt in range(4):
        try:
            if spec["mode"] == "responses":
                text, tool_call = run_responses(client, model, task)
            else:
                text, tool_call = run_chat(client, model, task)
            evaluation = evaluate_response(task, text, tool_call=tool_call)
            return {
                "provider": provider,
                "model": model,
                "task_id": task.task_id,
                "task_family": task.family,
                "status": "completed",
                "success": evaluation.success,
                "structured_valid": evaluation.structured_valid,
                "correctness": evaluation.correctness,
                "tool_call_correct": evaluation.tool_call_correct,
                "unnecessary_thinking": evaluation.unnecessary_thinking,
                "reason_codes": list(evaluation.reason_codes),
                "wall_time_seconds": round(time.monotonic() - started, 6),
                "response_sha256": response_checksum(text, tool_call),
                "retries": attempt,
                "ended_at": now(),
            }
        except Exception as exc:
            error = exc
            delay = retry_delay(exc, attempt)
            if delay is None or attempt == 3:
                break
            time.sleep(delay)
    message = str(error or "unknown error")
    return {
        "provider": provider,
        "model": model,
        "task_id": task.task_id,
        "task_family": task.family,
        "status": "failed",
        "success": False,
        "reason_codes": [f"transport_{type(error).__name__}"],
        "error": message[:300],
        "wall_time_seconds": round(time.monotonic() - started, 6),
        "retries": 3,
        "ended_at": now(),
    }


def persist(path: Path, document: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def load_seeded_records(seeds: list[Path], tasks: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current_corpus_sha256 = corpus_digest(tasks)
    seen: set[tuple[object, object, object]] = set()
    for seed in seeds:
        seed_document = json.loads(seed.read_text(encoding="utf-8"))
        if (
            seed_document.get("corpus_version") != CORPUS_VERSION
            or seed_document.get("corpus_sha256") != current_corpus_sha256
        ):
            raise ValueError(
                "seed "
                f"{seed} corpus mismatch: expected {CORPUS_VERSION}/{current_corpus_sha256}"
            )
        for row in seed_document.get("records", []):
            key = (row.get("provider"), row.get("model"), row.get("task_id"))
            if key not in seen:
                seen.add(key)
                records.append(row)
    return records


def summarize(document: dict[str, Any]) -> None:
    records = document["records"]
    selections: dict[str, Any] = {}
    task_ids = {task.task_id for task in benchmark_corpus()}
    completed_routes = {
        (provider, model)
        for provider in document["providers"]
        for model in document["providers"][provider]["models"]
        if {
            row["task_id"]
            for row in records
            if row["provider"] == provider
            and row["model"] == model
            and row.get("status") == "completed"
            and row.get("success") is True
        }
        == task_ids
    }
    for role, families in ROLE_FAMILIES.items():
        expected = set(families)
        candidates = []
        for provider in document["providers"]:
            for model in document["providers"][provider]["models"]:
                if (provider, model) not in completed_routes:
                    continue
                rows = [r for r in records if r["provider"] == provider and r["model"] == model and r["task_family"] in expected]
                if {r["task_family"] for r in rows if r.get("success") is True} == expected:
                    candidates.append({
                        "provider": provider,
                        "model": model,
                        "median_wall_time_seconds": round(statistics.median(r["wall_time_seconds"] for r in rows), 6),
                    })
        candidates.sort(key=lambda row: (row["median_wall_time_seconds"], row["provider"], row["model"]))
        selections[role] = {"status": "available", "candidates": candidates} if candidates else {"status": "unavailable", "candidates": []}
    document["selections"] = selections
    blocked_providers = sorted(
        {
            row["provider"]
            for row in records
            if row.get("status") == "environment-blocked"
        }
    )
    for provider, meta in document["providers"].items():
        meta["status"] = "environment-blocked" if provider in blocked_providers else "measured"
    document["environment_blocked_providers"] = blocked_providers
    # A document is only a clean, fully-exercised "completed" benchmark when
    # every selected provider actually ran. A provider with missing
    # credentials never had its routes exercised, so the receipt must not
    # claim the same clean "completed" status as a genuinely measured run.
    document["status"] = "completed_with_environment_blocks" if blocked_providers else "completed"
    document["ended_at"] = now()


def run_provider(
    provider: str,
    spec: dict[str, Any],
    document: dict[str, Any],
    output: Path,
    smoke: bool,
    qualified: set[tuple[str, str]] | None,
) -> None:
    if not spec["api_key"] and provider != "opencode-free":
        rows = [
            {
                "provider": provider,
                "model": model,
                "task_id": task.task_id,
                "task_family": task.family,
                "status": "environment-blocked",
                "success": False,
                "reason_codes": ["provider_credentials_missing"],
                "wall_time_seconds": 0.0,
                "retries": 0,
                "ended_at": now(),
            }
            for model in spec["models"]
            for task in (benchmark_corpus()[:1] if smoke else benchmark_corpus())
        ]
        with WRITE_LOCK:
            document["records"].extend(rows)
            persist(output, document)
            for row in rows:
                print(
                    json.dumps(
                        {
                            key: row[key]
                            for key in (
                                "provider",
                                "model",
                                "task_id",
                                "status",
                                "success",
                                "wall_time_seconds",
                            )
                        }
                    ),
                    flush=True,
                )
        return
    client = OpenAI(
        api_key=spec["api_key"],
        base_url=spec["base_url"],
        default_headers=spec["headers"],
        max_retries=0,
        timeout=120.0,
    )
    tasks = benchmark_corpus()[:1] if smoke else benchmark_corpus()
    completed = {(r["provider"], r["model"], r["task_id"]) for r in document["records"]}
    for model in spec["models"]:
        if qualified is not None and (provider, model) not in qualified:
            continue
        for task in tasks:
            if (provider, model, task.task_id) in completed:
                continue
            row = run_one(client, provider, spec, model, task)
            with WRITE_LOCK:
                document["records"].append(row)
                persist(output, document)
                print(
                    json.dumps(
                        {
                            key: row[key]
                            for key in (
                                "provider",
                                "model",
                                "task_id",
                                "status",
                                "success",
                                "wall_time_seconds",
                            )
                        }
                    ),
                    flush=True,
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--provider", action="append")
    parser.add_argument(
        "--model",
        action="append",
        metavar="PROVIDER=MODEL",
        help="Override a provider candidate list; repeat for multiple candidates",
    )
    parser.add_argument("--seed", type=Path, action="append", help="Seed smoke receipts; only passing models continue")
    args = parser.parse_args()
    specs = provider_specs()
    overrides: dict[str, list[str]] = {}
    for value in args.model or ():
        provider, separator, model = value.partition("=")
        if not separator or not provider or not model:
            parser.error(f"invalid --model {value!r}; expected PROVIDER=MODEL")
        if provider not in specs:
            parser.error(f"unknown provider in --model: {provider}")
        overrides.setdefault(provider, []).append(model)
    for provider, models in overrides.items():
        specs[provider] = {**specs[provider], "models": tuple(models)}
    selected = tuple(args.provider or specs.keys())
    unknown_providers = set(selected) - specs.keys()
    if unknown_providers:
        parser.error(f"unknown providers: {sorted(unknown_providers)}")
    if args.output.exists():
        document = json.loads(args.output.read_text(encoding="utf-8"))
    else:
        tasks = benchmark_corpus()
        try:
            seeded_records = load_seeded_records(args.seed or [], tasks)
        except ValueError as exc:
            parser.error(str(exc))
        document = {
            "schema_version": "hrl.provider_benchmark.v1",
            "classification": "diagnostic-only",
            "corpus_version": CORPUS_VERSION,
            "corpus_sha256": corpus_digest(tasks),
            "started_at": now(),
            "status": "running",
            "main_py_started": False,
            "providers": {name: {"models": list(specs[name]["models"]), "mode": specs[name]["mode"]} for name in selected},
            "records": seeded_records,
        }
        persist(args.output, document)
    qualified = None
    if args.seed:
        qualified = {
            (row["provider"], row["model"])
            for row in document["records"]
            if row.get("task_id") == "classify-20-v1" and row.get("success") is True
        }
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected)) as pool:
        futures = [
            pool.submit(run_provider, name, specs[name], document, args.output, args.smoke, qualified)
            for name in selected
        ]
        for future in futures:
            future.result()
    summarize(document)
    persist(args.output, document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
