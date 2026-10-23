"""Content-free egress findings using the final firewall predicates."""

from collections.abc import Mapping
from typing import Any, Sequence

from agent.llm_egress_firewall import (
    AnthropicThinkingReplaySegment,
    CodexReasoningReplaySegment,
    GeneratedContextSegment,
    GeneratedContextKey,
    LiteralSegment,
    OutboundText,
    SanitizedSegment,
    SourceBoundSegment,
    SourcePresentationSegment,
    UntrustedProvenanceSegment,
    ValidatedToolSyntaxSegment,
    _contains_exact_secret,
    _contains_private_absolute_path,
    _generated_context_text_for_base64_scan,
    _contains_secret,
    validate_tool_syntax,
)


def typed_mapping_key_violation_location(key, path, index, exact_secret_values):
    """Describe only key positions that the final renderer's scans reject."""
    text = key.text if isinstance(key, GeneratedContextKey) else key
    if not isinstance(text, str):
        return None
    reasons = set()
    if _contains_secret(text):
        reasons.add("secret_detected")
    if _contains_private_absolute_path(text):
        reasons.add("private_absolute_path")
    if exact_secret_values and _contains_exact_secret(text, exact_secret_values):
        reasons.add("exact_secret_detected")
    if not reasons:
        return None
    return (f"{path}.map[{index}].key", "MappingKey", len(text.encode("utf-8")),
            tuple(sorted(reasons)))


def content_free_violation_locations(value: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return structural indexes and reasons without returning request text."""
    from agent.llm_egress_firewall import _contains_canonical_base64

    locations = []
    seen = set()

    def visit(item: Any, path: str) -> None:
        if isinstance(item, str):
            reasons = []
            if _contains_secret(item):
                reasons.append("secret_detected")
            if _contains_canonical_base64(item):
                reasons.append("base64_payload")
            if _contains_private_absolute_path(item):
                reasons.append("private_absolute_path")
            if reasons:
                locations.append((path, tuple(reasons)))
            return
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen:
                locations.append((path, ("cyclic_container",)))
                return
            seen.add(identity)
            for index, (key, child) in enumerate(item.items()):
                visit(key, f"{path}.map[{index}].key")
                visit(child, f"{path}.map[{index}].value")
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            identity = id(item)
            if identity in seen:
                locations.append((path, ("cyclic_container",)))
                return
            seen.add(identity)
            for index, child in enumerate(item):
                visit(child, f"{path}.sequence[{index}]")

    visit(value, "$")
    return tuple(locations)


def typed_payload_violation_locations(
    value: Any,
    *,
    exact_secret_values: Sequence[str] = (),
) -> tuple[tuple[str, str, int, tuple[str, ...]], ...]:
    """Summarize unsafe typed segments without recording their text."""
    from agent.llm_egress_refusal_origins import (
        _joined_scan_segment_reasons,
        _typed_payload_exact_secret_match,
        _typed_payload_shape_reasons,
    )

    locations: list[tuple[str, str, int, tuple[str, ...]]] = []
    seen: set[int] = set()
    text_segments = (
        SanitizedSegment,
        GeneratedContextSegment,
        LiteralSegment,
        ValidatedToolSyntaxSegment,
        CodexReasoningReplaySegment,
        AnthropicThinkingReplaySegment,
        SourcePresentationSegment,
        SourceBoundSegment,
        UntrustedProvenanceSegment,
    )

    def visit(item: Any, path: str, joined_reasons=frozenset()) -> None:
        if isinstance(item, UntrustedProvenanceSegment):
            # Never expose the marker hash; retain only its structural reason.
            locations.append((path, "UntrustedProvenanceSegment", 0, ("untrusted_provenance",)))
            return
        if isinstance(item, (OutboundText, Mapping, list, tuple)):
            if id(item) in seen:
                return
            seen.add(id(item))
        if isinstance(item, OutboundText):
            joined = _joined_scan_segment_reasons(item.segments, exact_secret_values)
            for index, segment in enumerate(item.segments):
                visit(segment, f"{path}.segments[{index}]", joined.get(index, frozenset()))
            return
        if isinstance(item, text_segments):
            text = getattr(item, "text", None)
            if not isinstance(text, str):
                return
            reasons = set(_typed_payload_shape_reasons(item, text))
            if exact_secret_values and _typed_payload_exact_secret_match(
                item, text, exact_secret_values
            ):
                reasons.add("exact_secret_detected")
            reasons.update(joined_reasons)
            if isinstance(item, ValidatedToolSyntaxSegment) and "base64_payload" in reasons:
                try:
                    validate_tool_syntax(text, item.syntax_kind)
                except (TypeError, ValueError):
                    pass
                else:
                    reasons.remove("base64_payload")
            if isinstance(item, GeneratedContextSegment) and "base64_payload" in reasons:
                scanned = _generated_context_text_for_base64_scan(text)
                if not any(
                    "base64_payload" in found
                    for _, found in content_free_violation_locations(scanned)
                ):
                    reasons.remove("base64_payload")
            fixed_reasons = tuple(sorted(reasons))
            if fixed_reasons:
                locations.append((path, type(item).__name__, len(text.encode("utf-8")), fixed_reasons))
            return
        if isinstance(item, Mapping):
            for index, (key, child) in enumerate(item.items()):
                key_location = typed_mapping_key_violation_location(
                    key, path, index, exact_secret_values
                )
                if key_location is not None:
                    locations.append(key_location)
                visit(child, f"{path}.map[{index}].value")
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, f"{path}.sequence[{index}]")

    visit(value, "$")
    return tuple(locations)
