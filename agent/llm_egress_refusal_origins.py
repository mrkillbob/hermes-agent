"""Content-free origin attribution for blocked provider requests."""

from collections.abc import Mapping
from typing import Any

from agent.llm_egress_firewall import (
    OutboundText,
    SourceBoundSegment,
    SourcePresentationSegment,
    UntrustedProvenanceSegment,
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


def typed_payload_refusal_origins(value: Any) -> tuple[str, ...]:
    """Map rejected typed segments to fixed categories at protocol positions."""
    from agent.llm_egress_classifier import _typed_payload_violation_locations

    violations = _typed_payload_violation_locations(value)
    if not violations:
        return ("other",)
    violating_paths = {item[0] for item in violations}
    origins: set[str] = set()

    def visit(item: Any, path: str, inherited: str, *, collection=None, ancestors=frozenset()):
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
                if path == "$":
                    origin = _ROOT_ORIGINS.get(key, origin)
                    child_collection = _ROOT_COLLECTIONS.get(key)
                visit(child, f"{path}.map[{index}].value", origin,
                      collection=child_collection, ancestors=ancestors)
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                origin = _message_origin(child) if collection is not None and isinstance(child, Mapping) else inherited
                visit(child, f"{path}.sequence[{index}]", origin, ancestors=ancestors)

    visit(value, "$", "other")
    return tuple(sorted(origins or {"other"}))


def refusal_receipt_scope(agent, registry, owner, typed_body):
    """Bind a lazy, content-free attribution callback to an existing receipt."""
    from agent.source_provenance_diagnostics import receipt_scope

    return receipt_scope(
        agent, registry, owner,
        refusal_origins=lambda: typed_payload_refusal_origins(typed_body),
    )
