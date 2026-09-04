"""Bounded streaming client for the local Ollama chat API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Literal

from .types import BenchmarkTask, OllamaTaskResponse


OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
MAX_RESPONSE_CHARACTERS = 200_000


class OllamaClientError(RuntimeError):
    """Raised when one bounded local inference attempt is invalid or incomplete."""


def _tool_payload(task: BenchmarkTask) -> list[dict[str, object]] | None:
    if task.tool_schema_json is None:
        return None
    tools = json.loads(task.tool_schema_json)
    payload: list[dict[str, object]] = []
    for tool in tools:
        required = list(tool.get("required", []))
        properties = {
            name: {"type": "integer" if name == "score" else "string"} for name in required
        }
        payload.append(
            {
                "type": "function",
                "function": {
                    "name": str(tool["name"]),
                    "description": "Synthetic benchmark tool. Do not execute it.",
                    "parameters": {
                        "type": "object",
                        "required": required,
                        "properties": properties,
                    },
                },
            }
        )
    return payload


def _request_payload(
    model: str,
    task: BenchmarkTask,
    reasoning: Literal["low", "medium", "high"] | None,
) -> dict[str, object]:
    options: dict[str, object] = {"temperature": 0, "seed": 7}
    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Follow the local synthetic benchmark. Do not browse or call undeclared tools.",
            },
            {"role": "user", "content": task.prompt},
        ],
        "stream": True,
        "options": options,
    }
    if task.thinking_allowed:
        payload["think"] = reasoning or "low"
    else:
        payload["think"] = False
        options["chat_template_kwargs"] = {"enable_thinking": False}
    tools = _tool_payload(task)
    if tools:
        payload["tools"] = tools
    else:
        payload["format"] = task.output_schema
    return payload


def _seconds(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) / 1_000_000_000, 9)


def _normalize_tool_call(message: dict[str, Any]) -> dict[str, object] | None:
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or not calls or not isinstance(calls[0], dict):
        return None
    function = calls[0].get("function")
    if not isinstance(function, dict):
        return None
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not isinstance(arguments, dict):
        return None
    return {"name": str(function.get("name", "")), "arguments": arguments}


def run_ollama_task(
    model: str,
    task: BenchmarkTask,
    *,
    reasoning: Literal["low", "medium", "high"] | None = None,
    timeout: float = 300.0,
) -> OllamaTaskResponse:
    """Run exactly one local request with no retry or provider fallback."""

    if not model or any(character.isspace() for character in model):
        raise ValueError("Ollama model must be a non-empty exact inventory name")
    body = json.dumps(_request_payload(model, task, reasoning), separators=(",", ":")).encode(
        "utf-8"
    )
    request = urllib.request.Request(
        OLLAMA_CHAT_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    first_output_at: float | None = None
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_call: dict[str, object] | None = None
    terminal: dict[str, Any] | None = None
    character_count = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                if not raw_line.strip():
                    continue
                try:
                    chunk = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise OllamaClientError("Ollama returned a non-JSON stream chunk") from exc
                if not isinstance(chunk, dict):
                    raise OllamaClientError("Ollama returned a non-object stream chunk")
                message = chunk.get("message", {})
                if not isinstance(message, dict):
                    raise OllamaClientError("Ollama stream message was not an object")
                content = str(message.get("content", ""))
                thinking = str(message.get("thinking", ""))
                current_tool = _normalize_tool_call(message)
                if first_output_at is None and (content or thinking or current_tool):
                    first_output_at = time.monotonic()
                content_parts.append(content)
                thinking_parts.append(thinking)
                character_count += len(content) + len(thinking)
                if character_count > MAX_RESPONSE_CHARACTERS:
                    raise OllamaClientError("Ollama response exceeded the bounded output limit")
                if current_tool is not None and tool_call is None:
                    tool_call = current_tool
                if chunk.get("done") is True:
                    terminal = chunk
                    break
    except (OSError, urllib.error.URLError) as exc:
        raise OllamaClientError(f"local Ollama request failed: {type(exc).__name__}") from exc
    finished = time.monotonic()
    if terminal is None:
        raise OllamaClientError("Ollama stream ended without a terminal chunk")
    thinking_text = "".join(thinking_parts)
    if thinking_text and not task.thinking_allowed:
        raise OllamaClientError("Ollama emitted thinking content while thinking was disabled")

    eval_count = terminal.get("eval_count")
    eval_duration = _seconds(terminal.get("eval_duration"))
    tokens_per_second = None
    if isinstance(eval_count, int) and eval_duration and eval_duration > 0:
        tokens_per_second = round(eval_count / eval_duration, 6)
    return OllamaTaskResponse(
        response_text="".join(content_parts),
        thinking_text=thinking_text,
        tool_call=tool_call,
        wall_time_seconds=round(finished - started, 6),
        time_to_first_token_seconds=(
            round(first_output_at - started, 6) if first_output_at is not None else None
        ),
        prompt_eval_count=(
            int(terminal["prompt_eval_count"])
            if isinstance(terminal.get("prompt_eval_count"), int)
            else None
        ),
        eval_count=int(eval_count) if isinstance(eval_count, int) else None,
        load_duration_seconds=_seconds(terminal.get("load_duration")),
        prompt_eval_duration_seconds=_seconds(terminal.get("prompt_eval_duration")),
        eval_duration_seconds=eval_duration,
        total_duration_seconds=_seconds(terminal.get("total_duration")),
        tokens_per_second=tokens_per_second,
    )
