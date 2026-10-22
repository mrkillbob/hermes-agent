"""Exact-call-bound, content-free cron inventory projection for owned workers.

This admits inventory counts only. Job prompts, paths, names and credentials
never acquire provenance from a catalog bridge or a tool's claimed label.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from hashlib import sha256
from typing import Any, Mapping

from agent.llm_egress_firewall import (
    GeneratedContextSegment,
    OutboundText,
    SanitizedSegment,
    UntrustedProvenanceSegment,
    validate_sanitized_text,
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_object(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 32768:
        return None
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_object)
    except (ValueError, TypeError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _inventory_arguments(args: Any) -> bool:
    if not isinstance(args, dict) or args.get("action") != "list":
        return False
    if set(args) - {"action", "limit", "job"}:
        return False
    # The model handler ignores these legacy keys for list. Preserve the
    # original argument bytes and their scans; they confer no result authority.
    if "limit" in args and (type(args["limit"]) is not int or args["limit"] < 1):
        return False
    if "job" in args:
        if not isinstance(args["job"], str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,63}", args["job"]
        ):
            return False
        try:
            validate_sanitized_text(args["job"], max_bytes=256)
        except (TypeError, ValueError):
            return False
    return True


def _is_inventory_call(call: Mapping[str, Any]) -> bool:
    function = call.get("function")
    if call.get("type") != "function" or not isinstance(function, Mapping):
        return False
    if set(function) != {"name", "arguments"}:
        return False
    args = _json_object(function.get("arguments"))
    if function.get("name") == "cronjob_manage":
        return _inventory_arguments(args)
    if function.get("name") != "tool_call" or not isinstance(args, dict):
        return False
    calls = args.get("calls")
    if set(args) != {"calls"} or not isinstance(calls, list) or len(calls) != 1:
        return False
    nested = calls[0]
    return (
        isinstance(nested, dict)
        and set(nested) == {"name", "arguments"}
        and nested.get("name") == "cronjob_manage"
        and _inventory_arguments(nested.get("arguments"))
    )


def _bound_untrusted_result(typed: Any, content: Any) -> bool:
    if not isinstance(content, str):
        return False
    digest = sha256(content.encode("utf-8")).hexdigest()
    if isinstance(typed, UntrustedProvenanceSegment):
        return typed.content_sha256 == digest
    if not isinstance(typed, OutboundText) or len(typed.segments) != 2:
        return False
    marker, raw = typed.segments
    # Only the classifier's exact diagnostic wrapper is replaceable, never
    # adjacent source/private bytes or an arbitrary typed composition.
    return (
        isinstance(marker, UntrustedProvenanceSegment)
        and marker.content_sha256 == digest
        and isinstance(raw, SanitizedSegment)
        and raw.text == content
    )


def _inventory_projection(content: Any) -> str | None:
    result = _json_object(content)
    if (
        result is None
        or not {"success", "count", "jobs"} <= set(result)
        or set(result) - {"success", "count", "jobs", "gateway_running", "warning"}
    ):
        return None
    if (
        "gateway_running" in result
        and result["gateway_running"] is not None
        and type(result["gateway_running"]) is not bool
    ):
        return None
    if "warning" in result and not isinstance(result["warning"], str):
        return None
    count, jobs = result["count"], result["jobs"]
    if (
        result["success"] is not True
        or type(count) is not int
        or not isinstance(jobs, list)
        or count != len(jobs)
        or any(not isinstance(job, dict) for job in jobs)
    ):
        return None
    # Nonempty inventory must be reviewed locally: no job field is replayed.
    projected = {"success": True, "count": count}
    projected.update({"jobs": []} if not jobs else {"jobs_omitted": True})
    return json.dumps(projected, separators=(",", ":"))


def project_cron_list(
    body: Mapping[str, Any],
    typed_body: Mapping[str, Any],
    enabled: bool,
) -> Mapping[str, Any]:
    """Replace only a refused exact inventory result with its validated count.

    Requires one unique preceding call and one result with the same ID. No
    generic tool_call admission, source grant, model invocation or mutation.
    Other transports and unmatched content keep their original firewall verdict.
    """
    if not enabled:
        return typed_body
    messages, typed_messages = body.get("messages"), typed_body.get("messages")
    if not isinstance(messages, list) or not isinstance(typed_messages, list):
        return typed_body
    if len(messages) != len(typed_messages):
        return typed_body
    call_counts: Counter[str] = Counter()
    result_counts: Counter[str] = Counter()
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "assistant":
            calls = message.get("tool_calls")
            for call in calls if isinstance(calls, list) else ():
                if isinstance(call, Mapping) and isinstance(call.get("id"), str):
                    call_counts[call["id"]] += 1
        if message.get("role") == "tool" and isinstance(
            message.get("tool_call_id"), str
        ):
            result_counts[message["tool_call_id"]] += 1
    preceding: set[str] = set()
    output = list(typed_messages)
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "assistant":
            calls = message.get("tool_calls")
            for call in calls if isinstance(calls, list) else ():
                if isinstance(call, Mapping) and _is_inventory_call(call):
                    call_id = call.get("id")
                    if isinstance(call_id, str) and call_id:
                        preceding.add(call_id)
            continue
        call_id = message.get("tool_call_id")
        typed_message = typed_messages[index]
        if (
            message.get("role") != "tool"
            or not isinstance(call_id, str)
            or call_id not in preceding
            or call_counts[call_id] != 1
            or result_counts[call_id] != 1
            or any(
                message.get(label) not in (None, "cronjob_manage")
                for label in ("name", "tool_name")
            )
            or not isinstance(typed_message, Mapping)
            or not _bound_untrusted_result(
                typed_message.get("content"), message.get("content")
            )
        ):
            continue
        projection = _inventory_projection(message.get("content"))
        if projection is not None:
            output[index] = {
                **typed_message,
                "content": GeneratedContextSegment(projection),
            }
    return {**typed_body, "messages": output}
