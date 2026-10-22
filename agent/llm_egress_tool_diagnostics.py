"""Exact-call replay for bounded application diagnostics, never arbitrary results."""

from collections import Counter
from collections.abc import Mapping
import re
from typing import NamedTuple

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
        or (bool(name.strip()) and not _TOOL_NAME.fullmatch(name))
    ):
        return None
    from agent.conversation_loop import _invalid_tool_name_error_content

    if text != _invalid_tool_name_error_content(name, valid):
        return None
    if not name.strip():
        return SanitizedSegment(text)
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


class _Entry(NamedTuple):
    kind: str
    call_id: str
    name: str | None
    path: tuple
    value: Mapping
    native: bool = False


def _openai_calls(message, index):
    calls = message.get("tool_calls")
    for position, call in enumerate(calls if isinstance(calls, list) else ()):
        if not isinstance(call, Mapping) or not isinstance(call.get("id"), str):
            continue
        function = call.get("function")
        name = (
            function.get("name")
            if call.get("type") == "function" and isinstance(function, Mapping)
            else None
        )
        yield _Entry(
            "call",
            call["id"],
            name if isinstance(name, str) else None,
            (index, "tool_calls", position, "function"),
            function,
        )


def _native_entries(message, index):
    blocks = message.get("content")
    for position, block in enumerate(blocks if isinstance(blocks, list) else ()):
        if not isinstance(block, Mapping):
            continue
        path = (index, "content", position)
        if (
            message.get("role") == "assistant"
            and block.get("type") == "tool_use"
            and isinstance(block.get("id"), str)
        ):
            name = block.get("name")
            yield _Entry(
                "call",
                block["id"],
                name if isinstance(name, str) else None,
                path,
                block,
                True,
            )
        elif (
            message.get("role") == "user"
            and block.get("type") == "tool_result"
            and isinstance(block.get("tool_use_id"), str)
        ):
            yield _Entry("result", block["tool_use_id"], None, path, block, True)


def _frame_entries(messages):
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "assistant":
            yield from _openai_calls(message, index)
        elif message.get("role") == "tool" and isinstance(
            message.get("tool_call_id"), str
        ):
            yield _Entry("result", message["tool_call_id"], None, (index,), message)
        yield from _native_entries(message, index)


def _at_path(value, path):
    try:
        for key in path:
            value = value[key]
    except (KeyError, IndexError, TypeError):
        return None
    return value


def _replace_at(value, path, replacement):
    if not path:
        return replacement
    key, *rest = path
    copied = dict(value) if isinstance(value, Mapping) else list(value)
    copied[key] = _replace_at(value[key], rest, replacement)
    return copied


def _project_result(agent, entry, call, typed_result):
    name, raw = call.name, entry.value
    if (
        name is None
        or call.native != entry.native
        or (call.native and call.path[0] + 1 != entry.path[0])
        or any(raw.get(label) not in (None, name) for label in ("name", "tool_name"))
        or not isinstance(typed_result, Mapping)
        or not isinstance(typed_result.get("content"), UntrustedProvenanceSegment)
        or not isinstance(raw.get("content"), str)
    ):
        return None
    content = raw["content"]
    result = (
        _empty_read(content, getattr(agent, "valid_tool_names", None))
        if name == "read_file"
        else _diagnostic(agent, name, content)
    )
    if result is None:
        return None
    projected = {**typed_result, "content": result}
    if not name.strip():
        for label in ("name", "tool_name"):
            if raw.get(label) == name:
                projected[label] = SanitizedSegment(raw[label])
    return projected


def type_tool_diagnostics(agent, body, typed_body, enabled):
    """Retype only unique preceding call/result pairs across both wire formats."""
    if not enabled:
        return typed_body
    messages, typed = body.get("messages"), typed_body.get("messages")
    if (
        not isinstance(messages, list)
        or not isinstance(typed, list)
        or len(messages) != len(typed)
    ):
        return typed_body
    entries = list(_frame_entries(messages))
    calls = Counter(e.call_id for e in entries if e.kind == "call")
    results = Counter(e.call_id for e in entries if e.kind == "result")
    preceding = {}
    output = list(typed)
    for entry in entries:
        if entry.kind == "call":
            preceding[entry.call_id] = entry
            continue
        call = preceding.get(entry.call_id)
        if (
            not call
            or not entry.call_id
            or calls[entry.call_id] != 1
            or results[entry.call_id] != 1
        ):
            continue
        typed_call = _at_path(output, call.path)
        if not isinstance(typed_call, Mapping):
            continue
        projected = _project_result(agent, entry, call, _at_path(output, entry.path))
        if projected is not None:
            output = _replace_at(output, entry.path, projected)
            if not call.name.strip():
                # The fixed empty-name correction proves absence, not a new
                # identifier grammar. Preserve its exact blank bytes only here.
                output = _replace_at(
                    output,
                    call.path,
                    {**typed_call, "name": SanitizedSegment(call.name)},
                )
    return {**typed_body, "messages": output}
