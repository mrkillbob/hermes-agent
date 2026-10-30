"""Payload-shape checks for the LLM egress firewall."""

from collections.abc import Mapping
from typing import Any
import math


def is_strict_sanitized_only_payload(
    value: Any,
    *,
    seen: set[int] | None = None,
) -> tuple[bool, int]:
    """Recognize the one grantless remote shape approved by policy."""
    segment_result = _segment_status(value)
    if segment_result is not None:
        return segment_result
    return _container_status(value, seen)


def _segment_status(value):
    from agent.llm_egress_firewall import (
        AnthropicThinkingReplaySegment,
        CodexReasoningReplaySegment,
        GeneratedContextKey,
        GeneratedContextSegment,
        LiteralSegment,
        NativeMetadataSegment,
        OutboundText,
        SanitizedSegment,
        SourceBoundSegment,
        SourcePresentationSegment,
        UntrustedProvenanceSegment,
        ValidatedToolSyntaxSegment,
        _ANTHROPIC_THINKING_SIGNATURE_REPLAY,
        _CODEX_ENCRYPTED_REASONING_REPLAY,
        validate_tool_syntax,
    )

    if isinstance(value, (SanitizedSegment, NativeMetadataSegment)):
        return isinstance(value.text, str), 1 if isinstance(value.text, str) else 0
    if isinstance(value, GeneratedContextSegment):
        return isinstance(value.text, str), 1 if isinstance(value.text, str) else 0
    if isinstance(value, GeneratedContextKey):
        return isinstance(value.text, str), 0
    if isinstance(value, CodexReasoningReplaySegment):
        return (
            isinstance(value.text, str)
            and _CODEX_ENCRYPTED_REASONING_REPLAY.fullmatch(value.text) is not None,
            1 if isinstance(value.text, str) else 0,
        )
    if isinstance(value, AnthropicThinkingReplaySegment):
        return (
            isinstance(value.text, str)
            and _ANTHROPIC_THINKING_SIGNATURE_REPLAY.fullmatch(value.text) is not None,
            1 if isinstance(value.text, str) else 0,
        )
    if isinstance(value, UntrustedProvenanceSegment):
        return False, 0
    if isinstance(value, ValidatedToolSyntaxSegment):
        try:
            validate_tool_syntax(value.text, value.syntax_kind)
        except (TypeError, ValueError):
            return False, 0
        return True, 1
    if isinstance(value, LiteralSegment):
        return isinstance(value.text, str), 0
    if isinstance(value, (SourceBoundSegment, SourcePresentationSegment)):
        return False, 0
    return None


def _container_status(value, seen):
    from agent.llm_egress_firewall import GeneratedContextKey, OutboundText

    if isinstance(value, OutboundText):
        if not value.segments:
            return False, 0
        count = 0
        for segment in value.segments:
            allowed, segment_count = is_strict_sanitized_only_payload(segment, seen=seen)
            if not allowed:
                return False, 0
            count += segment_count
        return count > 0, count
    if value is None or isinstance(value, (bool, int)):
        return True, 0
    if isinstance(value, float):
        return math.isfinite(value), 0
    if isinstance(value, (str, bytes, bytearray, memoryview, set, frozenset)):
        return False, 0
    if seen is None:
        seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return False, 0
        seen.add(identity)
        count = 0
        for key, item in value.items():
            if not isinstance(key, (str, GeneratedContextKey)):
                return False, 0
            allowed, item_count = is_strict_sanitized_only_payload(item, seen=seen)
            if not allowed:
                return False, 0
            count += item_count
        return True, count
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            return False, 0
        seen.add(identity)
        count = 0
        for item in value:
            allowed, item_count = is_strict_sanitized_only_payload(item, seen=seen)
            if not allowed:
                return False, 0
            count += item_count
        return True, count
    return False, 0


def final_scan_attribution_inputs(typed_request, values, origins, capture_failed):
    """Keep attribution optional when rendering did not capture a complete map."""
    if typed_request is None or capture_failed:
        return (), ()
    return values, origins


def capture_scan_origin(origins, carrier, message_index):
    """Record an origin if possible; attribution allocation must stay optional."""
    try:
        origins.append((carrier, message_index))
    except MemoryError:
        return False
    return True


def append_snapshot_scan_inputs(snapshot_scans, scan_values, capture_origin):
    """Append verified grant scans and capture content-free aggregate origins."""
    from agent.llm_egress_firewall import _source_text_for_base64_scan

    encoded_scans = [
        _source_text_for_base64_scan(text, allow_fixed_source_literals=True)
        for text in snapshot_scans
    ]
    scan_values.extend(encoded_scans)
    for _ in encoded_scans:
        capture_origin("aggregate", None)
