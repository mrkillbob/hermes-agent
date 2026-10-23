"""Generic bounded passive Kanban snapshots for in-process plugin contexts."""

from contextlib import closing
from dataclasses import fields
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

_MAX_ROWS = 10_000
_MAX_ROW_BYTES = 1024 * 1024
_MAX_SCAN_BYTES = 8 * 1024 * 1024
_MAX_MARKER_BYTES = 64 * 1024
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


def _rows(path, key, prefix, row_projector=None, *, deadline=None):
    from hermes_cli import kanban_db as kb

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_ROW_BYTES)
        db.row_factory = sqlite3.Row
        deadline = _monotonic() + _SECONDS if deadline is None else deadline
        db.set_progress_handler(lambda: int(_monotonic() >= deadline), 1000)
        version = db.execute("PRAGMA data_version").fetchone()
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
            raw = json.dumps(task, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(result) >= _MAX_ROWS or len(raw.encode("utf-8")) > _MAX_ROW_BYTES:
                raise ValueError("complete snapshot bound exceeded")
            # Trusted in-process projection is parsing, never caller authority.
            # Validate each original row before retaining only its compact proof.
            projected = task if row_projector is None else row_projector(task)
            if not isinstance(projected, dict):
                raise ValueError("complete task projection required")
            encoded = json.dumps(projected, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            length = len(encoded.encode("utf-8"))
            size += length
            if (len(result) >= _MAX_ROWS or length > _MAX_ROW_BYTES
                    or size > _MAX_SCAN_BYTES or _monotonic() >= deadline):
                raise ValueError("complete snapshot bound exceeded")
            result.append(projected)
        if _monotonic() >= deadline:
            raise ValueError("snapshot deadline exceeded")
        db.execute("COMMIT")
        if db.execute("PRAGMA data_version").fetchone() != version:
            raise ValueError("task history changed during snapshot")
        return result


def _stat_identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _marker_identity(marker, deadline):
    """Bound marker memory and prove that the opened file stayed unchanged."""
    if _monotonic() >= deadline:
        raise ValueError("snapshot deadline exceeded")
    try:
        before = marker.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_MARKER_BYTES:
        raise ValueError("bounded regular board marker required")
    # Nonblocking open prevents a concurrent replacement with a FIFO from
    # waiting before fstat can reject it; ordinary regular-file reads are unchanged.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(marker, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if _stat_identity(opened) != _stat_identity(before):
            raise ValueError("board marker changed before read")
        data = stream.read(_MAX_MARKER_BYTES + 1)
        after = os.fstat(stream.fileno())
    if (len(data) > _MAX_MARKER_BYTES or len(data) != opened.st_size
            or _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(marker.stat()) != _stat_identity(after)
            or _monotonic() >= deadline):
        raise ValueError("board marker changed during bounded read")
    return _stat_identity(after), data


def _board_identity(root, slug, path, deadline):
    """Pin the existing file and ownership marker without exporting metadata."""
    marker = root / "kanban" / "boards" / slug / "board.json"
    database_stat = path.stat()
    return (database_stat.st_dev, database_stat.st_ino), _marker_identity(marker, deadline)


def readonly_snapshot(manager, board, *, idempotency_key=None, idempotency_prefix=None,
                      row_projector=None):
    """Return metadata (tasks=None), or one complete exact/prefix task snapshot."""
    from hermes_cli import kanban_db as kb

    try:
        deadline = _monotonic() + _SECONDS
        _selector(idempotency_key, idempotency_prefix)
        if row_projector is not None and (not callable(row_projector)
                or (idempotency_key is None and idempotency_prefix is None)):
            raise ValueError("in-process selected row projection required")
        root = _context_root(manager)
        slug = kb._normalize_board_slug(board)
        if not slug or not kb.board_exists(slug):
            raise ValueError("explicit existing board required")
        # Pin the file to the registered owner, not a later process-global read.
        directory = root if slug == kb.DEFAULT_BOARD else root / "kanban" / "boards" / slug
        path = (directory / "kanban.db").resolve(strict=True)
        if not path.is_file():
            raise ValueError("existing canonical task database required")
        identity = _board_identity(root, slug, path, deadline)
        tasks = None
        if idempotency_key is not None or idempotency_prefix is not None:
            tasks = _rows(path, idempotency_key, idempotency_prefix, row_projector,
                          deadline=deadline)
        if (_context_root(manager) != root or not kb.board_exists(slug)
                or path != (directory / "kanban.db").resolve(strict=True)
                or _board_identity(root, slug, path, deadline) != identity):
            raise ValueError("board ownership or database changed during snapshot")
        return {"control_root": str(root), "board": slug, "tasks": tasks}
    except (OSError, sqlite3.Error, TypeError, ValueError):
        # Errors can originate in SQLite values: never echo private task data.
        raise ValueError("passive Kanban snapshot unavailable") from None


class PassiveKanbanContext:
    """Public additive context surface; implementation stays in this sibling."""

    def kanban_readonly_snapshot(self, board: str, *, idempotency_key=None,
                                 idempotency_prefix=None, row_projector=None) -> dict:
        """Bounded passive task snapshot in this owner's unfenced Kanban scope.

        No selector returns context metadata with tasks=None; exact key or literal
        prefix returns a complete list including archived tasks, or raises.
        """
        return readonly_snapshot(self._manager, board, idempotency_key=idempotency_key,
                                 idempotency_prefix=idempotency_prefix, row_projector=row_projector)
