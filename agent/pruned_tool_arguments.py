"""Dispatch-boundary detection of compressed effectful tool-call arguments.

A fresh executor imports this stateless leaf without requiring new exports on
an older tool_dispatch_helpers module still cached by a running agent.
"""

from __future__ import annotations

from typing import Any

from agent.compression_marker import _COMPRESSION_MARKER_RE
from agent.tool_result_classification import tool_may_have_side_effect


def _context_pruned_argument_paths(tool_name: str, args: Any) -> list[str]:
    """Paths whose values contain model-visible context-compression artifacts.

    The compressor's current marker carries numeric omitted/total counts. Match
    that rendered shape rather than the prefix alone so Hermes can still edit
    source/docs that mention the compression marker constant or its template.
    Unknown/plugin/MCP tools stay effect-capable by default; known read-only
    tools may inspect or quote compressed history.
    """
    if not tool_may_have_side_effect(tool_name):
        return []

    found: list[str] = []

    def _walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            if _COMPRESSION_MARKER_RE.search(value):
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
