"""Cold-safe classification of saved tool data; never grants instruction authority."""

from __future__ import annotations

import json
from typing import Any

_PROVENANCE = "hermes_compaction_todo_state_v1"


def is_todo_state_result(message: Any) -> bool:
    """Recognize reference data without granting it recovery authority."""
    if not isinstance(message, dict) or message.get("role") != "tool":
        return False
    if message.get("_todo_snapshot_synthetic") is True:
        return True
    content = message.get("content")
    if not isinstance(content, str) or _PROVENANCE not in content[:512]:
        return False
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("provenance") == _PROVENANCE


def todo_steer_suffix(message: Any) -> str | None:
    """For structured tool data return only a legacy appended steer suffix.

    None means this is not recognizable structured tool data. An empty string means all
    its text is reference data. Never interpret markers inside the JSON body.
    """
    if is_todo_state_result(message):
        return ""
    if not isinstance(message, dict) or message.get("role") != "tool":
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    try:
        stripped = content.lstrip()
        data, end = json.JSONDecoder().raw_decode(stripped)
    except (ValueError, TypeError):
        return None
    if isinstance(data, (dict, list)):
        return stripped[end:]
    return None
