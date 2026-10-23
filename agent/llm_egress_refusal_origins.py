"""Content-free origin attribution for blocked provider requests."""

from collections.abc import Mapping
from typing import Any

from agent.llm_egress_firewall import (
    AnthropicThinkingReplaySegment,
    CodexReasoningReplaySegment,
    GeneratedContextSegment,
    LiteralSegment,
    OutboundText,
    SanitizedSegment,
    SourceBoundSegment,
    SourcePresentationSegment,
    UntrustedProvenanceSegment,
    ValidatedToolSyntaxSegment,
    _ANTHROPIC_THINKING_SIGNATURE_REPLAY,
    _CODEX_ENCRYPTED_REASONING_REPLAY,
    _contains_canonical_base64,
    _contains_exact_secret,
    _contains_private_absolute_path,
    _contains_secret,
)
from agent.llm_egress_content_diagnostics import content_free_violation_locations

_ORIGINS = frozenset({
    "tool_result", "source_bound", "assistant_history", "user_task",
    "system_or_schema", "other",
})
_ROOT_ORIGINS = {
    "tools": "system_or_schema",
    "functions": "system_or_schema",
    "system": "system_or_schema",
    "instructions": "system_or_schema",
    "input": "user_task",
}
_ROOT_COLLECTIONS = {"messages": "messages", "input": "input"}


def _rendered_non_source_text(segment, text):
    if isinstance(segment, CodexReasoningReplaySegment):
        return text if _CODEX_ENCRYPTED_REASONING_REPLAY.fullmatch(text) else ""
    if isinstance(segment, AnthropicThinkingReplaySegment):
        return text if _ANTHROPIC_THINKING_SIGNATURE_REPLAY.fullmatch(text) else ""
    if isinstance(segment, ValidatedToolSyntaxSegment):
        from agent.egress_tool_syntax import validate_tool_syntax

        try:
            return validate_tool_syntax(text, segment.syntax_kind)
        except (TypeError, ValueError):
            return ""
    return text


def _typed_payload_exact_secret_match(segment, text, exact_secret_values):
    if not isinstance(segment, (
        SanitizedSegment, GeneratedContextSegment, LiteralSegment,
        ValidatedToolSyntaxSegment,
    )):
        return False
    return _contains_exact_secret(
        _rendered_non_source_text(segment, text), exact_secret_values
    )


def _typed_payload_shape_reasons(segment, text):
    if isinstance(segment, (
        CodexReasoningReplaySegment,
        AnthropicThinkingReplaySegment,
        SourcePresentationSegment,
    )):
        # Final source scans depend on a request-bound grant and rendered fields;
        # this diagnostic helper must not treat the raw presentation as visible.
        return frozenset()
    scanned = (
        _rendered_non_source_text(segment, text)
        if isinstance(segment, ValidatedToolSyntaxSegment) else text
    )
    return frozenset(
        reason
        for _, reasons in content_free_violation_locations(scanned)
        for reason in reasons
    )


def _joined_scan_segment_reasons(segments, exact_secret_values=()):
    """Mirror final contiguous scan groups and attribute only their fixed findings."""
    matched = {}
    joined_kinds = (
        ((SanitizedSegment,), True),
        ((LiteralSegment, SanitizedSegment, GeneratedContextSegment,
          CodexReasoningReplaySegment, AnthropicThinkingReplaySegment,
          ValidatedToolSyntaxSegment), False),
    )

    for kinds, scan_base64 in joined_kinds:
        indexes, texts = [], []
        for index, segment in enumerate(segments):
            text = getattr(segment, "text", None)
            if isinstance(segment, kinds) and isinstance(text, str):
                indexes.append(index)
                texts.append(_rendered_non_source_text(segment, text))
                continue
            if len(indexes) > 1:
                _record_joined_scan_reasons(
                    matched, indexes, texts, scan_base64, exact_secret_values
                )
            indexes.clear()
            texts.clear()
        if len(indexes) > 1:
            _record_joined_scan_reasons(
                matched, indexes, texts, scan_base64, exact_secret_values
            )
    return {index: frozenset(reasons) for index, reasons in matched.items()}


def _record_joined_scan_reasons(matched, indexes, texts, scan_base64, exact_secret_values):
    combined = "".join(texts)
    reasons = set()
    if _contains_secret(combined):
        reasons.add("secret_detected")
    if _contains_private_absolute_path(combined):
        reasons.add("private_absolute_path")
    if scan_base64 and _contains_canonical_base64(combined):
        reasons.add("base64_payload")
    if exact_secret_values and _contains_exact_secret(combined, exact_secret_values):
        reasons.add("exact_secret_detected")
    if reasons:
        matched.setdefault(indexes[0], set()).update(reasons)


class SourceBoundRefusalSegment(UntrustedProvenanceSegment):
    """Untrusted source material whose provenance proof was rejected."""

    __slots__ = ()


class ToolResultRefusalSegment(UntrustedProvenanceSegment):
    """Untrusted tool output whose provenance proof was rejected."""

    __slots__ = ()


def _literal(value: Any) -> str | None:
    from agent.llm_egress_firewall import LiteralSegment

    if isinstance(value, LiteralSegment):
        return value.text
    if isinstance(value, SanitizedSegment):
        return value.text
    if isinstance(value, ValidatedToolSyntaxSegment):
        rendered = _rendered_non_source_text(value, value.text)
        return rendered if rendered == value.text else None
    return value if type(value) is str else None


def _validated_tool_result_refs(value: Any) -> frozenset[tuple[str, int]]:
    """Return result objects with one preceding call and one matching result."""
    if not isinstance(value, Mapping):
        return frozenset()
    calls: dict[tuple[str, str], list[int]] = {}
    results: dict[tuple[str, str], list[tuple[int, int]]] = {}
    position = 0

    def add_call(kind: str, call_id: Any) -> None:
        nonlocal position
        identifier = _literal(call_id)
        if identifier:
            calls.setdefault((kind, identifier), []).append(position)
        position += 1

    def add_result(kind: str, call_id: Any, item: Any) -> None:
        nonlocal position
        identifier = _literal(call_id)
        if identifier:
            results.setdefault((kind, identifier), []).append((position, id(item)))
        position += 1

    def scan_messages(messages: Any) -> None:
        if not isinstance(messages, (list, tuple)):
            return
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            role = _literal(message.get("role"))
            if role == "assistant":
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, (list, tuple)):
                    for call in tool_calls:
                        if (
                            isinstance(call, Mapping)
                            and _literal(call.get("type")) == "function"
                        ):
                            function = call.get("function")
                            if (
                                isinstance(function, Mapping)
                                and _literal(function.get("name"))
                                and _literal(function.get("arguments")) is not None
                            ):
                                add_call("chat", call.get("id"))
                content = message.get("content")
                if isinstance(content, (list, tuple)):
                    for block in content:
                        if (
                            isinstance(block, Mapping)
                            and _literal(block.get("type")) == "tool_use"
                            and _literal(block.get("name"))
                            and isinstance(block.get("input"), Mapping)
                        ):
                            add_call("anthropic", block.get("id"))
            elif role == "tool":
                add_result("chat", message.get("tool_call_id"), message)
            elif role == "user":
                content = message.get("content")
                if isinstance(content, (list, tuple)):
                    for block in content:
                        if isinstance(block, Mapping) and _literal(block.get("type")) == "tool_result":
                            add_result("anthropic", block.get("tool_use_id"), block)

    scan_messages(value.get("messages"))
    input_items = value.get("input")
    if isinstance(input_items, (list, tuple)):
        for item in input_items:
            if not isinstance(item, Mapping):
                continue
            item_type = _literal(item.get("type"))
            if item_type == "function_call":
                if (
                    _literal(item.get("name"))
                    and _literal(item.get("arguments")) is not None
                ):
                    add_call("responses_input", item.get("call_id"))
            elif item_type == "function_call_output":
                add_result("responses_input", item.get("call_id"), item)

    valid_refs = set()
    for key, result_entries in results.items():
        call_entries = calls.get(key, ())
        if (
            len(call_entries) == 1
            and len(result_entries) == 1
            and call_entries[0] < result_entries[0][0]
        ):
            valid_refs.add((key[0], result_entries[0][1]))
    return frozenset(valid_refs)


def _message_origin(
    message: Mapping[str, Any], valid_tool_result_refs: frozenset[tuple[str, int]]
) -> str:
    role = _literal(message.get("role"))
    if role in {"system", "developer"}:
        return "system_or_schema"
    if role == "assistant":
        return "assistant_history"
    if role == "tool":
        return "tool_result" if ("chat", id(message)) in valid_tool_result_refs else "other"
    if role == "user":
        return "user_task"
    return "other"


def _anthropic_content_origin(
    value: Mapping[str, Any],
    inherited: str,
    valid_tool_result_refs: frozenset[tuple[str, int]],
) -> str:
    block_type = value.get("type")
    protocol_type = _literal(block_type)
    if protocol_type is not None:
        block_type = protocol_type
    elif isinstance(block_type, SanitizedSegment):
        block_type = block_type.text
    if (
        block_type == "tool_result"
        and ("anthropic", id(value)) in valid_tool_result_refs
    ):
        return "tool_result"
    return inherited


def _sequence_child_state(
    child: Any,
    inherited: str,
    collection: str | None,
    valid_tool_result_refs: frozenset[tuple[str, int]],
):
    if not isinstance(child, Mapping):
        return inherited, False
    if collection in {"messages", "input"}:
        if collection == "input" and _literal(child.get("type")) == "function_call_output":
            origin = (
                "tool_result"
                if ("responses_input", id(child)) in valid_tool_result_refs
                else inherited
            )
            return origin, True
        return _message_origin(child, valid_tool_result_refs), True
    if collection == "anthropic_user_content":
        return _anthropic_content_origin(child, inherited, valid_tool_result_refs), False
    return inherited, False


def _typed_payload_refusal_origin_evidence(
    value: Any, *, exact_secret_values: tuple[str, ...] = ()
) -> tuple[tuple[str, ...], bool]:
    """Return fixed origins and whether the typed payload supplied evidence."""
    from agent.llm_egress_classifier import _typed_payload_violation_locations

    violations = _typed_payload_violation_locations(
        value, exact_secret_values=exact_secret_values
    )
    if not violations:
        return (), False
    valid_tool_result_refs = _validated_tool_result_refs(value)
    opaque_replay_types = {"CodexReasoningReplaySegment", "AnthropicThinkingReplaySegment"}
    violating_paths = {
        path for path, segment_type, _, reasons in violations
        if segment_type not in opaque_replay_types
        or {"exact_secret_detected", "secret_detected", "private_absolute_path"}.intersection(reasons)
    }
    origins: set[str] = set()

    def visit(
        item: Any,
        path: str,
        inherited: str,
        *,
        collection=None,
        protocol_message=False,
        ancestors=frozenset(),
    ):
        if isinstance(item, (OutboundText, Mapping, list, tuple)):
            if id(item) in ancestors:
                if path in violating_paths:
                    origins.add("other")
                return
            ancestors = ancestors | {id(item)}
        if isinstance(item, (
            SourceBoundRefusalSegment, SourceBoundSegment, SourcePresentationSegment
        )):
            if path in violating_paths:
                origins.add("source_bound")
            return
        if isinstance(item, ToolResultRefusalSegment):
            if path in violating_paths:
                origins.add(inherited if inherited in _ORIGINS else "other")
            return
        if isinstance(item, UntrustedProvenanceSegment):
            if path in violating_paths:
                origins.add("other")
            return
        if path in violating_paths:
            origins.add(inherited if inherited in _ORIGINS else "other")
        if isinstance(item, OutboundText):
            for index, segment in enumerate(item.segments):
                visit(segment, f"{path}.segments[{index}]", inherited, ancestors=ancestors)
        elif isinstance(item, Mapping):
            for index, (key, child) in enumerate(item.items()):
                origin = inherited
                child_collection = None
                child_protocol_message = False
                key_path = f"{path}.map[{index}].key"
                if key_path in violating_paths:
                    origins.add(inherited if inherited in _ORIGINS else "other")
                if path == "$":
                    origin = _ROOT_ORIGINS.get(key, origin)
                    child_collection = _ROOT_COLLECTIONS.get(key)
                elif (
                    protocol_message
                    and inherited == "user_task"
                    and key == "content"
                    and isinstance(child, (list, tuple))
                ):
                    child_collection = "anthropic_user_content"
                visit(child, f"{path}.map[{index}].value", origin,
                      collection=child_collection,
                      protocol_message=child_protocol_message,
                      ancestors=ancestors)
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                origin, is_protocol_message = _sequence_child_state(
                    child, inherited, collection, valid_tool_result_refs
                )
                visit(
                    child,
                    f"{path}.sequence[{index}]",
                    origin,
                    protocol_message=is_protocol_message,
                    ancestors=ancestors,
                )

    visit(value, "$", "other")
    return tuple(sorted(origins or {"other"})), True


def typed_payload_refusal_origins(
    value: Any, *, exact_secret_values: tuple[str, ...] = (),
    _include_evidence: bool = False,
) -> tuple[str, ...] | tuple[tuple[str, ...], bool]:
    """Map rejected typed segments to fixed categories at protocol positions."""
    evidence = _typed_payload_refusal_origin_evidence(
        value, exact_secret_values=exact_secret_values
    )
    if _include_evidence:
        return evidence
    origins, _ = evidence
    return origins or ("other",)


def refusal_receipt_scope(
    agent, registry, owner, typed_payload, *, exact_secret_values=()
):
    """Bind a lazy, content-free attribution callback to an existing receipt."""
    from agent.source_provenance_diagnostics import receipt_scope

    return receipt_scope(
        agent, registry, owner,
        refusal_origins=lambda: typed_payload_refusal_origins(
            typed_payload,
            exact_secret_values=exact_secret_values,
            _include_evidence=True,
        ),
    )
