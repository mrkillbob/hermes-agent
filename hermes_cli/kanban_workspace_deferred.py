"""Resume recorded shared-workspace cleanup when the last borrower finishes.

Only new, identity-bound deferrals participate. Legacy/unknown ownership and
changed directories are retained; this is not a general historical collector.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
from pathlib import Path


def _directory_identity(path: Path) -> dict:
    if path.is_symlink() or not path.is_dir():
        raise OSError("workspace identity is not a concrete directory")
    info = path.stat()
    return {"path": str(path.resolve(strict=True)), "device": info.st_dev, "inode": info.st_ino}


def _identity(conn, task_id, path) -> dict | None:
    from hermes_cli import kanban_db_workspace as ws
    from hermes_cli.kanban_db_connect import _main_db_file

    row = conn.execute(ws._WORKSPACE_ROW_SQL, (task_id,)).fetchone()
    database = _main_db_file(conn)
    if not row or not database or row["workspace_kind"] not in ws._REMOVABLE_KINDS:
        return None
    lexical = ws._lexical_path(path)
    if ws._lexical_path(row["workspace_path"]) != lexical:
        return None
    db = Path(database).resolve(strict=True)
    db_stat = db.stat()
    result = {
        "version": 1, "task_id": task_id, "board": str(db),
        "board_device": db_stat.st_dev, "board_inode": db_stat.st_ino,
        "kind": row["workspace_kind"], "lexical": str(lexical),
        "directory": _directory_identity(lexical), "branch": row["branch_name"],
    }
    if row["workspace_kind"] == "scratch":
        return result if ws._is_managed_scratch_path(lexical) else None
    common = ws._git_common_dir(lexical)
    admin = ws._git_abs_path(lexical, "--git-dir")
    branch = ws._git_current_branch(lexical)
    if not common or common.name != ".git" or not admin or admin == common or not branch:
        return None
    if row["branch_name"] and row["branch_name"] != branch:
        return None
    result.update(common=_directory_identity(common), admin=_directory_identity(admin), current_branch=branch)
    return result


def _defer_shared_workspace_cleanup(conn, task_id, path) -> bool:
    """Skip removal and record why, binding known sharing to this exact owner."""
    from hermes_cli import kanban_db_workspace as ws

    reason = ws._workspace_in_use_by_other(conn, task_id, path)
    if reason is None:
        return False
    ws._kb._log.warning("Deferring workspace cleanup for task %s at %s: %s", task_id, path, reason)
    payload = {"path": str(path), "reason": reason}
    if reason == "shared":
        try:
            identity = _identity(conn, task_id, path)
            if identity:
                payload["identity"] = identity
        except (OSError, ValueError, TypeError, sqlite3.Error):
            pass  # no identity means no automatic continuation
    try:
        ws._kb._append_event(conn, task_id, "workspace_cleanup_deferred_shared", payload)
    except sqlite3.Error:
        pass
    return True


def _pending(conn, key):
    from hermes_cli import kanban_db_workspace as ws

    rows = conn.execute(
        "SELECT t.id, t.workspace_path, t.workspace_kind, t.branch_name, e.payload "
        "FROM tasks t JOIN task_events e ON e.task_id=t.id "
        f"WHERE t.status IN {ws._TERMINAL_STATUSES_SQL} "
        "AND e.kind='workspace_cleanup_deferred_shared' "
        "AND e.id=(SELECT MAX(n.id) FROM task_events n WHERE n.task_id=t.id "
        "AND n.kind='workspace_cleanup_deferred_shared')",
    ).fetchall()
    pending = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
            identity = payload.get("identity")
            if (payload.get("reason") == "shared" and isinstance(identity, dict)
                    and ws._path_key(identity["directory"]["path"]) == key):
                pending.append((conn, dict(row), identity))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return pending


def _eligible(conn, row, expected):
    from hermes_cli import kanban_db_workspace as ws

    task_id, path = row["id"], row["workspace_path"]
    return (
        ws._is_terminal_task(conn, task_id)
        and not ws._has_active_children(conn, task_id)
        and _identity(conn, task_id, path) == expected
        and ws._workspace_in_use_by_other(conn, task_id, path) is None
    )


def resume_shared_cleanup(conn, task_id) -> None:
    """Best-effort continuation, with sibling DBs read-only and no recursion."""
    from hermes_cli import kanban_db_workspace as ws

    logger = ws._kb._log
    try:
        row = conn.execute(ws._WORKSPACE_ROW_SQL, (task_id,)).fetchone()
        if not row or not row["workspace_path"] or not ws._is_terminal_task(conn, task_id):
            return
        key = ws._path_key(Path(row["workspace_path"]).expanduser().resolve(strict=False))
        with contextlib.ExitStack() as stack:
            # Discover and read every board before removal. An unreadable board
            # leaves all candidates intact, even if a local owner looked free.
            boards = [conn]
            for database in ws._sibling_board_db_files(conn):
                other = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1.0)
                stack.callback(other.close)
                other.row_factory = sqlite3.Row
                boards.append(other)
            pending = [entry for board in boards for entry in _pending(board, key)]
            for owner_conn, owner, identity in pending:
                if not _eligible(owner_conn, owner, identity):
                    continue
                check = lambda: _eligible(owner_conn, owner, identity)
                if owner["workspace_kind"] == "worktree":
                    ws._cleanup_worktree_workspace(
                        owner["id"], owner["workspace_path"], owner["branch_name"], identity_check=check,
                    )
                elif owner["workspace_kind"] == "scratch":
                    ws.release_lsp_clients(owner["workspace_path"])
                    if check():
                        shutil.rmtree(owner["workspace_path"], ignore_errors=True)
    except Exception:
        logger.exception("Deferred workspace cleanup failed for task %s; retaining data", task_id)
