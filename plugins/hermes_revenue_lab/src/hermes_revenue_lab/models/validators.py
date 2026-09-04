"""Deterministic, fail-closed benchmark response validators."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from typing import Any

from .types import BenchmarkTask, TaskEvaluation


def _valid_required_fields(payload: object, schema: object) -> bool:
    if not isinstance(payload, dict) or not isinstance(schema, dict):
        return False
    required = schema.get("required", [])
    return isinstance(required, list) and all(str(key) in payload for key in required)


def _has_unexpected_thinking(task: BenchmarkTask, raw: str, payload: object | None) -> bool:
    if task.thinking_allowed:
        return False
    lowered = raw.lower()
    if "<think" in lowered or "</think>" in lowered:
        return True
    return isinstance(payload, dict) and any(
        str(key).lower() in {"thinking", "analysis", "reasoning_content"} for key in payload
    )


def _safe_collector_source(source: object) -> bool:
    if not isinstance(source, str) or len(source) > 4000:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    disallowed = (
        ast.AsyncFunctionDef,
        ast.Await,
        ast.ClassDef,
        ast.Delete,
        ast.Global,
        ast.Import,
        ast.ImportFrom,
        ast.Lambda,
        ast.Nonlocal,
        ast.Raise,
        ast.Try,
        ast.With,
        ast.Yield,
        ast.YieldFrom,
    )
    allowed_named_calls = {"int", "len", "range"}
    allowed_methods = {"append", "isdigit", "split", "splitlines"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != "parse_prices":
        return False
    for node in ast.walk(tree):
        if isinstance(node, disallowed):
            return False
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return False
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("__") or node.attr not in allowed_methods
        ):
            return False
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id not in allowed_named_calls:
                return False
            if isinstance(node.func, ast.Attribute) and node.func.attr not in allowed_methods:
                return False
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                return False
    namespace: dict[str, Any] = {"__builtins__": {"int": int, "len": len, "range": range}}
    try:
        exec(compile(tree, "<benchmark-collector>", "exec"), namespace)
        result = namespace["parse_prices"]("a,10\nb,bad\nc,30\nmissing")
    except Exception:
        return False
    return result == [10, 30]


def _score_payload(task: BenchmarkTask, payload: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    expected = task.expected
    if not isinstance(expected, dict):
        return 0.0, ("invalid_expected_fixture",)

    if task.family == "repair_collector":
        if payload.get("function") != "parse_prices":
            return 0.0, ("incorrect_output",)
        if not _safe_collector_source(payload.get("python")):
            return 0.0, ("unsafe_python",)
        return 1.0, ()

    if task.family == "structured_audit":
        required_match = all(payload.get(key) == value for key, value in expected.items())
        remedies = payload.get("remedies")
        if not required_match or not isinstance(remedies, list) or len(remedies) != 2:
            return 0.0, ("incorrect_output",)
        return 1.0, ()

    if dict(payload) == expected:
        return 1.0, ()
    return 0.0, ("incorrect_output",)


def evaluate_response(
    task: BenchmarkTask,
    response_text: str,
    *,
    tool_call: Mapping[str, object] | None = None,
) -> TaskEvaluation:
    """Evaluate one response without another model or external service."""

    reasons: list[str] = []
    parsed: object | None = None
    if tool_call is not None and task.family == "select_tool":
        parsed = {"tool": tool_call.get("name"), "arguments": tool_call.get("arguments")}
    else:
        try:
            parsed = json.loads(response_text)
        except (json.JSONDecodeError, TypeError):
            reasons.append("invalid_json")

    unexpected_thinking = _has_unexpected_thinking(task, response_text, parsed)
    if unexpected_thinking:
        reasons.append("unexpected_thinking")

    structured_valid = _valid_required_fields(parsed, task.output_schema)
    if not structured_valid and "invalid_json" not in reasons:
        reasons.append("schema_invalid")

    correctness: float | None = None
    score_reasons: tuple[str, ...] = ()
    if structured_valid and isinstance(parsed, dict):
        correctness, score_reasons = _score_payload(task, parsed)
        reasons.extend(score_reasons)

    tool_call_correct: bool | None = None
    if task.family == "select_tool":
        tool_call_correct = correctness == 1.0

    success = (
        structured_valid
        and correctness == 1.0
        and not unexpected_thinking
        and (tool_call_correct is not False)
    )
    return TaskEvaluation(
        structured_valid=structured_valid,
        correctness=correctness,
        tool_call_correct=tool_call_correct,
        unnecessary_thinking=unexpected_thinking,
        success=success,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )
