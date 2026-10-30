"""Content-free revision binding for an operator-reviewed correction snapshot.

A matching digest detects staleness; it grants no admission, privacy clearance,
project ownership or permission to change a card's status.
"""
from __future__ import annotations

import hashlib
import json
import re

_TABLES = frozenset({
    "tasks", "task_links", "task_comments", "task_events", "task_runs",
    "task_attachments", "kanban_notify_subs",
})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def task_revision(board: str, task_id: str, rows: dict) -> str:
    """Hash the complete selected rows, including existing refusal/history data.

    ``rows`` comes from the same frozen ``_selected_task_rows`` snapshot used by
    the transfer. A separately sampled event watermark cannot bind edited old
    payloads or changes between an early task read and that snapshot.
    """
    if not board or not task_id or set(rows) != _TABLES:
        raise ValueError("binding revision requires a complete scoped snapshot")
    tasks = rows["tasks"]
    if len(tasks) != 1 or tasks[0].get("id") != task_id:
        raise ValueError("binding revision requires one matching task")
    try:
        encoded = json.dumps({"board": board, "task_id": task_id, "rows": rows},
                             sort_keys=True, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("binding revision cannot be represented") from None
    if len(encoded) > 8 * 1024 * 1024:
        raise ValueError("binding revision snapshot exceeds size limit")
    return hashlib.sha256(encoded).hexdigest()


def require_task_revision(expected: str | None, board: str, task_id: str, rows: dict) -> None:
    if not isinstance(expected, str) or _DIGEST.fullmatch(expected) is None:
        raise ValueError("binding correction requires a reviewed task revision")
    if task_revision(board, task_id, rows) != expected:
        raise ValueError("reviewed task revision changed")
