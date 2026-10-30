"""Actionable adapter startup and reconnect diagnostics."""

from __future__ import annotations

from gateway.config import Platform

def _adapter_unavailable_message(platform: Platform, *, retrying: bool = True) -> str:
    """Actionable ``adapter_unavailable`` status text, shared by startup and the reconnect watcher so
    ``hermes status`` keeps the plugin/deps/credentials hint after the first retry."""
    message = (
        f"No adapter available for enabled {platform.value}; check the plugin, dependencies, and credentials."
    )
    return f"{message} Retrying in the background." if retrying else message
