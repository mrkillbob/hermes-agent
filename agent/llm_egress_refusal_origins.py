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
    _contains_exact_secret,
    content_free_violation_locations,
)

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


def _joined_exact_secret_segment_indexes(segments, exact_secret_values):
    """Identify pieces covered by final construction's joined secret scans."""
    matched = set()
    joined_kinds = ((SanitizedSegment,), (LiteralSegment, SanitizedSegment,
        GeneratedContextSegment, CodexReasoningReplaySegment,
        AnthropicThinkingReplaySegment, ValidatedToolSyntaxSegment))

    for kinds in joined_kinds:
        indexes, texts = [], []
        for index, segment in enumerate(segments):
            text = getattr(segment, "text", None)
            if isinstance(segment, kinds) and isinstance(text, str):
                indexes.append(index)
                texts.append(_rendered_non_source_text(segment, text))
            else:
                if len(indexes) > 1 and _contains_exact_secret("".join(texts), exact_secret_values):
                    matched.add(indexes[0])
                indexes.clear()
                texts.clear()
        if len(indexes) > 1 and _contains_exact_secret("".join(texts), exact_secret_values):
            matched.add(indexes[0])
    return frozenset(matched)


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
    return value if type(value) is str else None


def _message_origin(message: Mapping[str, Any]) -> str:
    role = _literal(message.get("role"))
    item_type = _literal(message.get("type"))
    if role in {"system", "developer"}:
        return "system_or_schema"
    if role == "assistant":
        return "assistant_history"
    if role == "tool" or item_type in {"tool_result", "function_call_output"}:
        return "tool_result"
    if role == "user":
        return "user_task"
    return "other"


def _anthropic_content_origin(value: Mapping[str, Any], inherited: str) -> str:
    block_type = value.get("type")
    protocol_type = _literal(block_type)
    if protocol_type is not None:
        block_type = protocol_type
    elif isinstance(block_type, SanitizedSegment):
        block_type = block_type.text
    if block_type == "tool_result" and "tool_use_id" in value:
        return "tool_result"
    return inherited


def _sequence_child_state(child: Any, inherited: str, collection: str | None):
    if not isinstance(child, Mapping):
        return inherited, False
    if collection in {"messages", "input"}:
        return _message_origin(child), True
    if collection == "anthropic_user_content":
        return _anthropic_content_origin(child, inherited), False
    return inherited, False


def typed_payload_refusal_origins(
    value: Any, *, exact_secret_values: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Map rejected typed segments to fixed categories at protocol positions."""
    from agent.llm_egress_classifier import _typed_payload_violation_locations

    violations = _typed_payload_violation_locations(
        value, exact_secret_values=exact_secret_values
    )
    if not violations:
        return ("other",)
    opaque_replay_types = {"CodexReasoningReplaySegment", "AnthropicThinkingReplaySegment"}
    violating_paths = {
        path for path, segment_type, _, reasons in violations
        if segment_type not in opaque_replay_types or "exact_secret_detected" in reasons
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
                    child, inherited, collection
                )
                visit(
                    child,
                    f"{path}.sequence[{index}]",
                    origin,
                    protocol_message=is_protocol_message,
                    ancestors=ancestors,
                )

    visit(value, "$", "other")
    return tuple(sorted(origins or {"other"}))


def refusal_receipt_scope(
    agent, registry, owner, typed_payload, *, exact_secret_values=()
):
    """Bind a lazy, content-free attribution callback to an existing receipt."""
    from agent.source_provenance_diagnostics import receipt_scope

    return receipt_scope(
        agent, registry, owner,
        refusal_origins=lambda: typed_payload_refusal_origins(
            typed_payload, exact_secret_values=exact_secret_values
        ),
    )
