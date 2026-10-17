"""Generic bounded passive Kanban snapshots for in-process plugin contexts."""

from contextlib import closing
from dataclasses import fields
import json
import os
from pathlib import Path
import sqlite3
import time

_MAX_ROWS = 10_000
_MAX_ROW_BYTES = 1024 * 1024
_MAX_SCAN_BYTES = 8 * 1024 * 1024
_SECONDS = 1.0
_monotonic = time.monotonic
_PROTECTED = frozenset({
    "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK",
    "HERMES_KANBAN_GOAL_MODE", "HERMES_KANBAN_GOAL_MAX_TURNS",
    "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_KANBAN_DB",
    "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_KANBAN_ATTACHMENTS_ROOT",
})


def _canonical(path):
    value = Path(path).expanduser()
    if not value.is_absolute():
        raise ValueError("absolute owner scope required")
    return value.resolve()


def _context_root(manager):
    from hermes_constants import get_default_hermes_root, get_hermes_home
    from hermes_cli import kanban_db as kb

    if _PROTECTED.intersection(os.environ) or kb._explicit_board_intent_pinned():
        raise ValueError("native worker or path context is fenced")
    home = _canonical(manager.home_path)
    if home != _canonical(manager.scope_key) or home != _canonical(get_hermes_home()):
        raise ValueError("active plugin owner differs")
    root = _canonical(get_default_hermes_root(home=home))
    if root != _canonical(get_default_hermes_root()) or root != _canonical(kb.kanban_home()):
        raise ValueError("native Kanban root differs from owner")
    if "HERMES_CONTROL_HOME" in os.environ and _canonical(os.environ["HERMES_CONTROL_HOME"]) != root:
        raise ValueError("inherited control root differs")
    return root


def _selector(key, prefix):
    if key is not None and prefix is not None:
        raise ValueError("exact key and literal prefix are exclusive")
    value = key if key is not None else prefix
    if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 512 or "\0" in value):
        raise ValueError("bounded text selector required")


def _rows(path, key, prefix):
    from hermes_cli import kanban_db as kb

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_ROW_BYTES)
        db.row_factory = sqlite3.Row
        deadline = _monotonic() + _SECONDS
        db.set_progress_handler(lambda: int(_monotonic() >= deadline), 1000)
        db.execute("BEGIN")
        columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        if not {field.name for field in fields(kb.Task)}.issubset(columns):
            raise ValueError("incomplete task schema")
        query, parameters = ("idempotency_key = ?", (key,)) if key is not None else (
            "substr(idempotency_key,1,?) = ?", (len(prefix), prefix))
        selected = db.execute("SELECT * FROM tasks WHERE " + query + " ORDER BY id", parameters)
        result, size = [], 0
        for row in selected:
            task = dict(row)
            encoded = json.dumps(task, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            length = len(encoded.encode("utf-8"))
            size += length
            if (len(result) >= _MAX_ROWS or length > _MAX_ROW_BYTES
                    or size > _MAX_SCAN_BYTES or _monotonic() >= deadline):
                raise ValueError("complete snapshot bound exceeded")
            result.append(task)
        if _monotonic() >= deadline:
            raise ValueError("snapshot deadline exceeded")
        return result


def readonly_snapshot(manager, board, *, idempotency_key=None, idempotency_prefix=None):
    """Return metadata (tasks=None), or one complete exact/prefix task snapshot."""
    from hermes_cli import kanban_db as kb

    try:
        _selector(idempotency_key, idempotency_prefix)
        root = _context_root(manager)
        slug = kb._normalize_board_slug(board)
        if not slug or not kb.board_exists(slug):
            raise ValueError("explicit existing board required")
        # Pin the file to the registered owner, not a later process-global read.
        directory = root if slug == kb.DEFAULT_BOARD else root / "kanban" / "boards" / slug
        path = (directory / "kanban.db").resolve(strict=True)
        if not path.is_file():
            raise ValueError("existing canonical task database required")
        tasks = None
        if idempotency_key is not None or idempotency_prefix is not None:
            tasks = _rows(path, idempotency_key, idempotency_prefix)
        return {"control_root": str(root), "board": slug, "tasks": tasks}
    except (OSError, sqlite3.Error, TypeError, ValueError):
        # Errors can originate in SQLite values: never echo private task data.
        raise ValueError("passive Kanban snapshot unavailable") from None


class PassiveKanbanContext:
    """Public additive context surface; implementation stays in this sibling."""

    def kanban_readonly_snapshot(self, board: str, *, idempotency_key=None, idempotency_prefix=None) -> dict:
        """Bounded passive task snapshot in this owner's unfenced Kanban scope.

        No selector returns context metadata with tasks=None; exact key or literal
        prefix returns a complete list including archived tasks, or raises.
        """
        return readonly_snapshot(self._manager, board, idempotency_key=idempotency_key,
                                 idempotency_prefix=idempotency_prefix)
