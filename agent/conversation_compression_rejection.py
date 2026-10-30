"""Comparison and user feedback for unchanged compaction candidates."""

from __future__ import annotations

import contextlib
from typing import Any


def _strip_marker_for_comparison(msgs: Any) -> Any:
    """Copy ``msgs`` with the ``_db_persisted`` marker removed for no-op comparison.
    Live dicts carry the marker while ``compress()`` output is swept, so a raw ``==`` would misclassify an
    identical no-op copy as progress. Non-list inputs and non-dict entries pass through unchanged."""
    from agent.context_compressor import _DB_PERSISTED_MARKER
    if not isinstance(msgs, list):
        return msgs
    return [{k: v for k, v in m.items() if k != _DB_PERSISTED_MARKER} if isinstance(m, dict) else m for m in msgs]


def warn_no_progress(agent: Any) -> None:
    """Report this rejected attempt without failing its unchanged-history return."""
    with contextlib.suppress(Exception):
        agent._emit_warning(
            "⚠ Compression made no progress. No messages were dropped by this attempt. "
            "Run /compress to retry, or /new to start a fresh session."
        )
