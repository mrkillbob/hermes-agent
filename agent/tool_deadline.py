"""Transport timeout extension that loads safely beside cached N-1 dispatch helpers."""

from __future__ import annotations


def _extend_tool_timeout(tool_name: str, default: float | None) -> float | None:
    """Never cut a registered transport budget short with the generic executor."""
    from tools.registry import registry

    entry = registry.get_entry(tool_name)
    # N-1 registry entries predate execution_timeout. Their unadvertised
    # budget retains the existing default; modern entries can extend it.
    advertised = getattr(entry, "execution_timeout", None)
    if default is None or advertised is None:
        return default
    return max(default, advertised)
