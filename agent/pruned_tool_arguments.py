"""Dispatch-boundary detection of compressed effectful tool-call arguments.

A fresh executor imports this stateless leaf without requiring new exports on
an older tool_dispatch_helpers module still cached by a running agent.
"""

from __future__ import annotations

import re
from typing import Any

from agent.compression_marker import _COMPRESSION_MARKER_PREFIX
from agent.tool_result_classification import tool_may_have_side_effect


# Derive the matcher from an export already present in cached pre-upgrade modules.
# A freshly loaded dispatch leaf must not require a new marker-module export.
_COMPRESSION_MARKER_ARTIFACT_RE = re.compile(
    re.escape(_COMPRESSION_MARKER_PREFIX) + r"\s+\d[\d,]*"
)


def _context_pruned_argument_paths(tool_name: str, args: Any) -> list[str]:
    """Paths whose values contain model-visible context-compression artifacts.

    A minted marker is identified by its prefix plus the first rendered numeric
    count. This catches a marker cut short before its fixed sentence while still
    letting Hermes edit source/docs that mention the bare prefix or template.
    Unknown/plugin/MCP tools stay effect-capable by default; known read-only
    tools may inspect or quote compressed history.
    """
    if not tool_may_have_side_effect(tool_name):
        return []

    found: list[str] = []

    def _walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            if _COMPRESSION_MARKER_ARTIFACT_RE.search(value):
                found.append(path)
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                child_path = f"{path}.{key_text}" if key_text.isidentifier() else f"{path}[{key_text!r}]"
                _walk(child, child_path)
            return
        if isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                _walk(child, f"{path}[{index}]")

    _walk(args, "$")
    return found
