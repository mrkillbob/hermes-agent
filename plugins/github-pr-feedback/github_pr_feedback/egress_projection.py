"""Remote-safe terminal projections owned by the PR-feedback plugin."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Mapping

from agent.llm_egress_runtime import register_terminal_replay_projection
from agent.message_sanitization import tool_result_id_variants
from agent.llm_egress_firewall import redact_remote_unsafe_text
from agent.redact import redact_sensitive_text


_TERMINAL_SUBCOMMANDS = frozenset(
    {
        "inspect-pr",
        "complete-feedback",
        "retire-feedback",
        "submit-review",
        "status",
        "inspect-ci",
    }
)
_TERMINAL_RESULT_KEYS = frozenset(
    {
        "base_branch",
        "base_sha",
        "codex_retrigger_status",
        "event",
        "expected_head_sha",
        "fallback",
        "feedback_body_excerpt",
        "feedback_id",
        "feedback_is_bot",
        "feedback_kind",
        "feedback_reviewer",
        "head_ref_name",
        "head_repository",
        "head_sha",
        "local_ci_status",
        "number",
        "observed_head_sha",
        "pr_number",
        "pr_state",
        "state",
        "task_id",
        "reason",
        "repository",
        "resolved_head_sha",
        "review_thread_resolved",
        "status",
        "error_excerpt",
        "receipt_id",
        "manifest_digest",
        "handoff_reason",
        "handoff_status",
        "repair_status",
        "retryable",
        "command_count",
        "failed_commands",
        "failed_output_digests",
        "failure_reason",
        "ci_mode",
    }
)


def _terminal_call_ids(value: Any) -> frozenset[str]:
    """Recognize governed PR-feedback terminal commands with JSON status output."""

    recognized: set[str] = set()

    def is_hermes_launcher_token(token: str) -> bool:
        return Path(token).name == "hermes" or token == "<private-path>"

    def command_is_pr_feedback(arguments: Any) -> bool:
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            command = parsed.get("command") if isinstance(parsed, Mapping) else None
            tokens = shlex.split(command) if isinstance(command, str) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        for index, token in enumerate(tokens):
            if (
                token == "github-pr-feedback"
                and index > 0
                and (
                    tokens[max(0, index - 2):index] == ["-m", "hermes_cli.main"]
                    or is_hermes_launcher_token(tokens[index - 1])
                )
            ):
                return (
                    index + 1 < len(tokens)
                    and tokens[index + 1] in _TERMINAL_SUBCOMMANDS
                )
        return False

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            direct_function = item.get("function")
            direct_name = (
                direct_function.get("name")
                if isinstance(direct_function, Mapping)
                else item.get("name")
            )
            if item.get("type") in {"function", "function_call"} and direct_name == "terminal":
                arguments = (
                    direct_function.get("arguments")
                    if isinstance(direct_function, Mapping)
                    else item.get("arguments")
                )
                call_id = item.get("call_id") or item.get("id")
                if command_is_pr_feedback(arguments) and isinstance(call_id, str):
                    recognized.update(tool_result_id_variants(call_id))
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return frozenset(recognized)


def _terminal_result(output: str) -> str:
    """Replay bounded JSON status from governed PR-feedback commands."""

    def safe_failure_excerpt(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        safe = redact_remote_unsafe_text(
            redact_sensitive_text(value, force=True, redact_url_credentials=True)
        )
        encoded = safe.encode("utf-8")
        if len(encoded) > 1200:
            safe = encoded[:1190].decode("utf-8", errors="ignore") + "\n<truncated>"
        return safe

    exit_code = None
    text = output
    failure_excerpt = None
    try:
        parsed = json.loads(output)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, Mapping):
        maybe_exit = parsed.get("exit_code")
        if isinstance(maybe_exit, int):
            exit_code = maybe_exit
        for key in ("stderr", "error"):
            failure_excerpt = safe_failure_excerpt(parsed.get(key))
            if failure_excerpt:
                break
        for key in ("stdout", "output", "content"):
            value = parsed.get(key)
            if isinstance(value, str):
                text = value
                break
    payload: dict[str, object] | None = None
    decoder = json.JSONDecoder()
    for line in reversed(str(text or "").splitlines() or [str(text or "")]):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            candidate, _end = decoder.raw_decode(stripped)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(candidate, Mapping):
            payload = {}
            for key, value in candidate.items():
                if key not in _TERMINAL_RESULT_KEYS:
                    continue
                if key in {"receipt_id", "manifest_digest"} and (
                    not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                ):
                    continue
                if value is None or isinstance(value, (bool, int)):
                    payload[str(key)] = value
                    continue
                if key in {"failed_commands", "failed_output_digests"} and isinstance(value, list):
                    bounded = [item for item in value[:16] if isinstance(item, str) and len(item) <= 200]
                    payload[str(key)] = bounded
                    continue
                if not isinstance(value, str):
                    continue
                limit = 1800 if key == "feedback_body_excerpt" else 200
                if len(value) <= limit:
                    payload[str(key)] = value
            break
    if failure_excerpt is None and exit_code not in (None, 0) and payload is None:
        diagnostics = [line for line in str(text or "").splitlines() if re.search(
            r"(?:ModuleNotFoundError:|ImportError:|command not found|No such file or directory)",
            line,
        )]
        failure_excerpt = safe_failure_excerpt("\n".join(diagnostics[:4]))
    replay: dict[str, object] = {
        "terminal_result": "github_pr_feedback",
        "exit_code": exit_code,
        "raw_output": "omitted_from_remote_replay",
    }
    if payload is not None:
        replay["json"] = payload
    if isinstance(parsed, Mapping):
        session_id = parsed.get("session_id")
        if isinstance(session_id, str) and re.fullmatch(r"proc_[0-9a-f]{12}", session_id):
            replay["session_id"] = session_id
            pid = parsed.get("pid")
            if type(pid) is int and 0 < pid < 2**31:
                replay["pid"] = pid
    if failure_excerpt:
        replay["error_excerpt"] = failure_excerpt
    return json.dumps(replay, sort_keys=True, separators=(",", ":"))


def register() -> None:
    """Register this plugin's bounded terminal result projection."""

    register_terminal_replay_projection(
        "github-pr-feedback",
        call_id_resolver=_terminal_call_ids,
        result_projector=_terminal_result,
    )
