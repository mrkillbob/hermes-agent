"""Content-free egress findings using the final firewall predicates."""

from collections.abc import Mapping
from typing import Any

from agent.llm_egress_firewall import (
    GeneratedContextKey,
    _contains_exact_secret,
    _contains_private_absolute_path,
    _contains_secret,
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
