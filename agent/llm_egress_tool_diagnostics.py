"""Exact-call replay for bounded application diagnostics, never arbitrary results."""

from collections import Counter
from collections.abc import Mapping
import re

from agent.llm_egress_cron import _json_object
from agent.llm_egress_firewall import (
    GeneratedContextSegment,
    OutboundText,
    SanitizedSegment,
    UntrustedProvenanceSegment,
)
from agent.llm_egress_terminal import _segment_protected_context

_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_EMPTY_READ = {
    "content": "",
    "total_lines": 0,
    "file_size": 0,
    "truncated": False,
    "hint": "File is empty (0 bytes).",
    "is_binary": False,
    "is_image": False,
    "not_found": False,
}


def _diagnostic(agent, name, text):
    valid = getattr(agent, "valid_tool_names", None)
    if (
        not isinstance(valid, (set, frozenset))
        or not valid
        or len(valid) > 512
        or not all(isinstance(n, str) and _TOOL_NAME.fullmatch(n) for n in valid)
        or name in valid
        or not _TOOL_NAME.fullmatch(name)
    ):
        return None
    from agent.conversation_loop import _invalid_tool_name_error_content

    if text != _invalid_tool_name_error_content(name, valid):
        return None
    catalog = _segment_protected_context(
        ", ".join(sorted(valid)), (), {}, sanitized_cap=32768
    )
    catalog_parts = (
        catalog.segments if isinstance(catalog, OutboundText) else (catalog,)
    )
    # The model-supplied invalid name stays ordinary scanned text, even when
    # it resembles a valid identifier. Only the owner catalog gets protocol typing.
    return OutboundText(
        (
            SanitizedSegment("Tool '"),
            SanitizedSegment(name),
            SanitizedSegment("' does not exist. Available tools: "),
            *catalog_parts,
        )
    )


def _empty_read(text, available):
    if not isinstance(available, (set, frozenset)) or "read_file" not in available:
        return None
    parsed = _json_object(text)
    if parsed != _EMPTY_READ or any(
        type(parsed[k]) is not type(v) for k, v in _EMPTY_READ.items()
    ):
        return None
    return GeneratedContextSegment(text)


def _call_names(messages):
    counts = Counter()
    names = {}
    results = Counter()
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        calls = (
            message.get("tool_calls") if message.get("role") == "assistant" else None
        )
        for call in calls if isinstance(calls, list) else ():
            if not isinstance(call, Mapping) or not isinstance(call.get("id"), str):
                continue
            cid = call["id"]
            counts[cid] += 1
            fn = call.get("function")
            if (
                call.get("type") == "function"
                and isinstance(fn, Mapping)
                and isinstance(fn.get("name"), str)
            ):
                names[cid] = fn["name"]
        cid = message.get("tool_call_id")
        if message.get("role") == "tool" and isinstance(cid, str):
            results[cid] += 1
    return counts, results, names


def type_tool_diagnostics(agent, body, typed_body, enabled):
    """Keep exact diagnostic bytes only after unique preceding-call validation."""
    if not enabled:
        return typed_body
    messages, typed = body.get("messages"), typed_body.get("messages")
    if (
        not isinstance(messages, list)
        or not isinstance(typed, list)
        or len(messages) != len(typed)
    ):
        return typed_body
    counts, results, names = _call_names(messages)
    seen = set()
    output = list(typed)
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "assistant":
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                seen.update(
                    call.get("id")
                    for call in calls
                    if isinstance(call, Mapping) and isinstance(call.get("id"), str)
                )
            continue
        cid = message.get("tool_call_id")
        name = names.get(cid) if isinstance(cid, str) else None
        typed_message = typed[index]
        if (
            message.get("role") != "tool"
            or not name
            or not cid
            or cid not in seen
            or counts[cid] != 1
            or results[cid] != 1
            or any(
                message.get(label) not in (None, name)
                for label in ("name", "tool_name")
            )
            or not isinstance(typed_message, Mapping)
            or not isinstance(typed_message.get("content"), UntrustedProvenanceSegment)
            or not isinstance(message.get("content"), str)
        ):
            continue
        content = message["content"]
        result = (
            _empty_read(content, getattr(agent, "valid_tool_names", None))
            if name == "read_file"
            else _diagnostic(agent, name, content)
        )
        if result is not None:
            output[index] = {**typed_message, "content": result}
    return {**typed_body, "messages": output}
