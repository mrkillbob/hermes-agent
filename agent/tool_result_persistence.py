"""Tool-result persistence gates safe to import during a mixed-version upgrade."""

from __future__ import annotations

from typing import Any


def _stamp_data_use_if_required(message: dict, data_use) -> None:
    # An N-1 worker may retain message_metadata from before data-use support.
    # Empty requirements need no new helper; a nonempty one must still fail
    # closed before the result is appended or persisted.
    if data_use:
        from agent.message_metadata import stamp_data_use
        stamp_data_use(message, sorted(data_use))


def _sync_rewritten_tool_rows_if_needed(agent, batch: list[dict], contents_before: list[Any]) -> int:
    # Preserve the persistence helper's early exits without importing a new
    # symbol from an N-1 cached session_persistence module. A changed durable
    # row still requires the writer; never silently accept a stale replay.
    session_db = getattr(agent, "_session_db", None)
    if (getattr(agent, "_persist_disabled", False) or not getattr(agent, "session_id", None)
            or not callable(getattr(session_db, "rewrite_pruned_rows", None))):
        return 0
    from agent.context_compressor import _DB_PERSISTED_MARKER
    if not any(msg.get(_DB_PERSISTED_MARKER) and msg.get("role") == "tool"
               and msg.get("content") != before for msg, before in zip(batch, contents_before)):
        return 0
    from agent.session_persistence import sync_rewritten_tool_rows
    return sync_rewritten_tool_rows(agent, batch, contents_before)
