"""Kanban board export / import — move a whole board between machines.

Backs ``hermes kanban export|import``, the ``/boards/{slug}/export`` and
``/boards/import`` REST endpoints, and the desktop board switcher. Archive
layout (``<slug>.tar.gz``, one top-level dir named for the source slug):
``manifest.json`` (format/version/provenance/counts), ``board.json`` (display
metadata, machine-local fields stripped), ``kanban.db`` (consistent snapshot),
``attachments/<task>/…`` (unless --no-attachments), ``logs/<task>.log`` (only
with --include-logs).

Two things make this more than ``tar czf`` of the board directory: the DB is
live (WAL mode, dispatcher may be mid-write) so export uses SQLite's online
backup instead of a file copy that would miss the ``-wal`` sidecar; and rows
carry machine-local state (claims, PIDs, heartbeats, absolute paths, gateway
chat subscriptions, session ids) that would import a stranger's claims or push
events into a stranger's Telegram thread — scrubbed on export and re-scrubbed
on import (an archive is untrusted); see :func:`_scrub_local_state` and
:func:`_relocate_imported_rows`. Imports always land as a **new** board (slug
auto-suffixes on collision), never ``default``, so the importer can ignore the
default board's split on-disk layout.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.archive_safe import (
    archive_root_dirs,
    copy_regular_files,
    make_targz,
    safe_extract_targz,
)

ARCHIVE_FORMAT = "hermes-kanban-board"
ARCHIVE_FORMAT_VERSION = 1

# Statuses from which the dispatcher can still act on a task. A task whose
# workspace cannot be rebuilt on this machine is parked in ``triage`` only
# if it is in one of these — terminal and already-parked tasks are left
# alone rather than having their history rewritten.
_DISPATCHABLE_STATUSES = ("ready", "running", "todo", "scheduled")
_COUNTED_TABLES = ("tasks", "task_links", "task_comments", "task_events", "task_runs", "task_attachments")


def _placeholders(items) -> str:
    return ", ".join("?" * len(items))


def _remove_merged_file(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _remove_source_task_rows(board: str, task_ids: list[str]) -> None:
    placeholders = _placeholders(task_ids)
    with kbc.connect_closing(board=board) as conn, kb.write_txn(conn):
        rows = conn.execute(
            f"SELECT id, status, claim_lock, current_run_id FROM tasks WHERE id IN ({placeholders})",
            task_ids,
        ).fetchall()
        if len(rows) != len(task_ids):
            raise RuntimeError("source cards changed during migration")
        if any(row["status"] == "running" or row["claim_lock"] or row["current_run_id"] for row in rows):
            raise ValueError("source board gained a live task claim during migration")
        conn.execute(f"DELETE FROM task_links WHERE parent_id IN ({placeholders}) OR child_id IN ({placeholders})", [*task_ids, *task_ids])
        for table in ("kanban_notify_subs", "task_comments", "task_events", "task_runs", "task_attachments", "tasks"):
            conn.execute(f'DELETE FROM "{table}" WHERE task_id IN ({placeholders})' if table != "tasks" else
                         f'DELETE FROM "{table}" WHERE id IN ({placeholders})', task_ids)


def _prune_verified_copy(source: str, destination: str, task_ids: list[str]) -> dict[str, int]:
    """Finish a previously copied migration after verifying both card records."""
    placeholders = _placeholders(task_ids)
    counts: dict[str, int] = {"tasks": len(task_ids)}
    with kbc.connect_closing(board=source) as src, kbc.connect_closing(board=destination) as dst:
        src.row_factory = dst.row_factory = sqlite3.Row
        source_tasks = {row["id"]: row for row in src.execute(
            f"SELECT * FROM tasks WHERE id IN ({placeholders})", task_ids,
        )}
        dest_tasks = {row["id"]: row for row in dst.execute(
            f"SELECT * FROM tasks WHERE id IN ({placeholders})", task_ids,
        )}
        if set(source_tasks) != set(task_ids) or set(dest_tasks) != set(task_ids):
            raise ValueError("cannot remove source cards until every destination card is present")
        runtime_columns = {"claim_lock", "claim_expires", "worker_pid", "worker_started_at",
                           "last_heartbeat_at", "current_run_id"}
        for task_id in task_ids:
            if any(source_tasks[task_id][key] != dest_tasks[task_id][key]
                   for key in source_tasks[task_id].keys() if key not in runtime_columns):
                raise ValueError(f"destination card does not match source: {task_id}")
        for table in ("task_comments", "task_runs", "task_attachments"):
            for task_id in task_ids:
                source_count = int(src.execute(f"SELECT COUNT(*) FROM {table} WHERE task_id=?", (task_id,)).fetchone()[0])
                dest_count = int(dst.execute(f"SELECT COUNT(*) FROM {table} WHERE task_id=?", (task_id,)).fetchone()[0])
                if source_count != dest_count:
                    raise ValueError(f"destination {table} history does not match source for {task_id}")
                counts[table] = counts.get(table, 0) + source_count
        for task_id in task_ids:
            source_links = {tuple(row) for row in src.execute(
                "SELECT parent_id, child_id FROM task_links WHERE parent_id=? OR child_id=?", (task_id, task_id))}
            dest_links = {tuple(row) for row in dst.execute(
                "SELECT parent_id, child_id FROM task_links WHERE parent_id=? OR child_id=?", (task_id, task_id))}
            if source_links != dest_links:
                raise ValueError(f"destination task links do not match source for {task_id}")
        event_counts = []
        for task_id in task_ids:
            source_count = int(src.execute("SELECT COUNT(*) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0])
            dest_count = int(dst.execute("SELECT COUNT(*) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0])
            if dest_count != source_count + 1:
                raise ValueError(f"destination task event history does not match source for {task_id}")
            event_counts.append(source_count)
        counts["task_events"] = sum(event_counts)
    _remove_source_task_rows(source, task_ids)
    return counts


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _snapshot_db(source: Path, target: Path) -> None:
    """Consistent copy of ``source`` via the online-backup API (a file copy
    would miss pages still in the ``-wal`` sidecar and could tear)."""
    with contextlib.closing(sqlite3.connect(str(source))) as src, \
            contextlib.closing(sqlite3.connect(str(target))) as dst:
        src.backup(dst)


def _scrub_local_state(conn: sqlite3.Connection) -> None:
    """Strip machine-local runtime state (claims, PIDs, and above all the
    gateway chat ids subscribed to task events). Caller owns the transaction.
    Run on export and again on import (an archive is untrusted input)."""
    conn.execute("DELETE FROM kanban_notify_subs")
    conn.execute(
        """
        UPDATE tasks
           SET claim_lock           = NULL,
               claim_expires        = NULL,
               worker_pid           = NULL,
               current_run_id       = NULL,
               last_heartbeat_at    = NULL,
               session_id           = NULL,
               project_id           = NULL,
               consecutive_failures = 0,
               last_failure_error   = NULL
        """
    )
    # A task caught mid-run is not running anywhere the importer can see.
    # Send it back to the queue rather than shipping a phantom claim.
    conn.execute("UPDATE tasks SET status = 'ready' WHERE status = 'running'")
    conn.execute(
        """
        UPDATE task_runs
           SET status            = 'released',
               outcome           = COALESCE(outcome, 'reclaimed'),
               ended_at          = COALESCE(ended_at, ?),
               last_heartbeat_at = NULL
         WHERE status = 'running'
        """,
        (int(time.time()),),
    )
    conn.execute("UPDATE task_runs SET claim_lock = NULL, worker_pid = NULL")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _count_rows(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in _COUNTED_TABLES}


def export_board(
    board: Optional[str],
    output_path: str,
    *,
    include_attachments: bool = True,
    include_logs: bool = False,
) -> dict[str, Any]:
    """Export ``board`` to a ``tar.gz`` (suffix optional on ``output_path``);
    returns a summary dict. Workspaces are never included — large,
    machine-local, rebuilt on demand."""
    slug = kb._normalize_board_slug(board) or kb.get_current_board()
    if not kb.board_exists(slug):
        raise ValueError(f"board {slug!r} does not exist")

    db_path = kb.kanban_db_path(slug)
    if not db_path.exists():
        raise FileNotFoundError(f"board {slug!r} has no database at {db_path}")

    base = str(Path(output_path).expanduser()).removesuffix(".tar.gz").removesuffix(".tgz")
    Path(base).parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        staged = Path(tmpdir) / slug
        staged.mkdir(parents=True)

        _snapshot_db(db_path, staged / "kanban.db")
        # The snapshot is a private file with no other writers, so plain
        # commit/close is enough — no need for the board DB's WAL dance.
        with contextlib.closing(sqlite3.connect(str(staged / "kanban.db"))) as snapshot:
            _scrub_local_state(snapshot)
            snapshot.commit()
            counts = _count_rows(snapshot)

        meta = kb.read_board_metadata(slug)
        # Both name a location on the exporting machine; the importer
        # resolves its own.
        meta.pop("db_path", None)
        meta["default_workdir"] = None
        meta["project_id"] = None
        _write_json(staged / "board.json", meta)

        attachments = copy_regular_files(kb.attachments_root(slug), staged / "attachments") if include_attachments else 0
        logs = copy_regular_files(kb.worker_logs_dir(slug), staged / "logs") if include_logs else 0

        from hermes_cli.version_info import get_version_info

        manifest = {
            "format": ARCHIVE_FORMAT,
            "format_version": ARCHIVE_FORMAT_VERSION,
            "board": slug,
            "board_name": meta.get("name") or slug,
            "exported_at": int(time.time()),
            "hermes_version": get_version_info().base_version,
            "includes": {"attachments": bool(include_attachments), "logs": bool(include_logs)},
            "counts": {**counts, "attachment_files": attachments, "log_files": logs},
        }
        _write_json(staged / "manifest.json", manifest)

        archive = make_targz(base, tmpdir, slug)

    return {
        "board": slug,
        "archive": archive,
        "size": Path(archive).stat().st_size,
        "counts": manifest["counts"],
    }


def export_board_workspaces(board: str, output_path: str) -> dict[str, Any]:
    """Preserve regular workspace files before an empty board is deleted."""
    slug = kb._normalize_board_slug(board)
    base = str(Path(output_path).expanduser()).removesuffix(".tar.gz").removesuffix(".tgz")
    Path(base).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hermes-kanban-workspaces-") as tmpdir:
        staged = Path(tmpdir) / "workspaces"
        staged.mkdir()
        files = copy_regular_files(kb.workspaces_root(slug), staged)
        archive = make_targz(base, tmpdir, "workspaces")
    return {"archive": archive, "files": files, "size": Path(archive).stat().st_size}


def merge_board(source: str, destination: str, backup_path: str, *, task_ids: list[str] | None = None) -> dict[str, Any]:
    """Copy every task and its history into an existing board, then verify it.

    The source is retained. Callers may remove it only after checking the
    returned counts and the independently exported recovery archive.
    """
    source = kb._normalize_board_slug(source)
    destination = kb._normalize_board_slug(destination)
    if not source or not destination or source == destination:
        raise ValueError("source and destination must be different existing board slugs")
    for slug in (source, destination):
        if not kb.board_exists(slug):
            raise ValueError(f"board {slug!r} does not exist")
    archive = export_board(source, backup_path, include_attachments=True, include_logs=True)
    source_path = kb.kanban_db_path(source)
    with contextlib.ExitStack() as cleanup, tempfile.TemporaryDirectory(prefix="hermes-kanban-merge-") as temp_dir:
        snapshot_path = Path(temp_dir) / "source.sqlite3"
        _snapshot_db(source_path, snapshot_path)
        with contextlib.closing(sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)) as src, \
                kbc.connect_closing(board=destination) as dst:
            src.row_factory = sqlite3.Row
            dst.row_factory = sqlite3.Row
            all_tasks = src.execute("SELECT * FROM tasks ORDER BY created_at, id").fetchall()
            all_ids = [str(row["id"]) for row in all_tasks]
            if task_ids is not None and len(set(task_ids)) != len(task_ids):
                raise ValueError("task_ids contains duplicates")
            selected = set(all_ids if task_ids is None else task_ids)
            missing = selected - set(all_ids)
            if missing:
                raise ValueError(f"source task does not exist: {sorted(missing)[0]}")
            ids = [task_id for task_id in all_ids if task_id in selected]
            tasks = [row for row in all_tasks if row["id"] in selected]
            if not ids:
                return {"source": source, "destination": destination, "tasks": 0,
                        "counts": {name: 0 for name in _COUNTED_TABLES}, "backup": archive["archive"]}
            if any(row["status"] == "running" or row["claim_lock"] or row["current_run_id"] for row in tasks):
                raise ValueError("source board has live task claims; wait for workers to finish before merging")
            placeholders = _placeholders(ids)
            overlap = dst.execute(f"SELECT id FROM tasks WHERE id IN ({placeholders})", ids).fetchall()
            if overlap:
                overlap_ids = {row["id"] for row in overlap}
                if overlap_ids != set(ids):
                    raise ValueError(f"task id already exists on destination: {overlap[0]['id']}")
                counts = _prune_verified_copy(source, destination, ids)
                return {"source": source, "destination": destination, "tasks": len(ids),
                        "counts": {**{name: 0 for name in _COUNTED_TABLES}, **counts},
                        "backup": archive["archive"], "already_copied": True}
            source_tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in source_tables - {"tasks", "task_links", "task_comments", "task_events", "task_runs",
                                            "task_attachments", "kanban_notify_subs", "sqlite_sequence"}:
                if src.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                    raise ValueError(f"board-scoped data requires a dedicated migration: {table}")
            table_counts: dict[str, int] = {}
            run_id_map: dict[int, int] = {}
            link_rows = src.execute(
                f"SELECT * FROM task_links WHERE parent_id IN ({placeholders}) OR child_id IN ({placeholders})",
                [*ids, *ids],
            ).fetchall()
            for row in link_rows:
                for linked_id in (row["parent_id"], row["child_id"]):
                    if linked_id not in ids and not dst.execute(
                        "SELECT 1 FROM tasks WHERE id=?", (linked_id,)
                    ).fetchone():
                        raise ValueError(
                            f"task selection splits dependency {row['parent_id']} -> {row['child_id']}; "
                            "merge all linked cards together"
                        )
            attachment_rows = src.execute(
                f"SELECT * FROM task_attachments WHERE task_id IN ({placeholders})", ids
            ).fetchall()
            for row in attachment_rows:
                original = Path(row["stored_path"])
                source_blob = original if original.is_absolute() else kb.attachments_root(source) / row["task_id"] / original
                if not source_blob.is_file():
                    raise ValueError(f"task attachment is missing: {source_blob}")
                target_dir = kb.task_attachments_dir(row["task_id"], destination)
                target_dir.mkdir(parents=True, exist_ok=True)
                target_blob = target_dir / original.name
                if target_blob.exists():
                    raise ValueError(f"destination attachment already exists: {target_blob}")
                shutil.copy2(source_blob, target_blob)
                cleanup.callback(_remove_merged_file, target_blob)
                if target_blob.read_bytes() != source_blob.read_bytes():
                    raise ValueError(f"attachment verification failed: {target_blob}")

            source_logs = kb.worker_logs_dir(source)
            target_logs = kb.worker_logs_dir(destination)
            for task_id in ids:
                for log in source_logs.glob(task_id + "*"):
                    target_log = target_logs / log.name
                    if target_log.exists():
                        raise ValueError(f"destination worker log already exists: {target_log}")
                    target_logs.mkdir(parents=True, exist_ok=True)
                    if log.is_dir():
                        shutil.copytree(log, target_log)
                    else:
                        shutil.copy2(log, target_log)
                    cleanup.callback(_remove_merged_file, target_log)

            with kb.write_txn(dst):
                def copy_rows(table: str, rows, *, omit_id: bool = False) -> dict[int, int]:
                    if not rows:
                        table_counts[table] = 0
                        return {}
                    columns = [column for column in rows[0].keys() if not (omit_id and column == "id")]
                    query = f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({", ".join("?" for _ in columns)})'
                    mapping: dict[int, int] = {}
                    for row in rows:
                        cursor = dst.execute(query, tuple(row[column] for column in columns))
                        if omit_id:
                            mapping[int(row["id"])] = int(cursor.lastrowid)
                    table_counts[table] = len(rows)
                    return mapping

                copy_rows("tasks", tasks)
                run_rows = src.execute(f"SELECT * FROM task_runs WHERE task_id IN ({placeholders}) ORDER BY id", ids).fetchall()
                run_id_map = copy_rows("task_runs", run_rows, omit_id=True)
                event_rows = src.execute(f"SELECT * FROM task_events WHERE task_id IN ({placeholders}) ORDER BY id", ids).fetchall()
                if event_rows:
                    normalized = []
                    for row in event_rows:
                        item = dict(row)
                        if row["run_id"] is not None and int(row["run_id"]) not in run_id_map:
                            raise ValueError(f"task event {row['id']} references a missing run {row['run_id']}")
                        item["run_id"] = run_id_map.get(int(row["run_id"])) if row["run_id"] is not None else None
                        normalized.append(item)
                    copy_rows("task_events", normalized, omit_id=True)
                else:
                    table_counts["task_events"] = 0
                comment_rows = src.execute(f"SELECT * FROM task_comments WHERE task_id IN ({placeholders}) ORDER BY id", ids).fetchall()
                copy_rows("task_comments", comment_rows, omit_id=True)
                for task_id, old_run_id in src.execute(
                    f"SELECT id, current_run_id FROM tasks WHERE id IN ({placeholders}) AND current_run_id IS NOT NULL", ids
                ):
                    if old_run_id not in run_id_map:
                        raise ValueError(f"task {task_id} references a missing run {old_run_id}")
                    dst.execute("UPDATE tasks SET current_run_id=? WHERE id=?", (run_id_map[old_run_id], task_id))
                for row in link_rows:
                    dst.execute("INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)", (row["parent_id"], row["child_id"]))
                table_counts["task_links"] = len(link_rows)
                for row in attachment_rows:
                    source_name = Path(row["stored_path"]).name
                    landed = kb.task_attachments_dir(row["task_id"], destination) / source_name
                    dst.execute(
                        "INSERT INTO task_attachments (task_id, filename, stored_path, content_type, size, uploaded_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (row["task_id"], row["filename"], str(landed.resolve()), row["content_type"], row["size"], row["uploaded_by"], row["created_at"]),
                    )
                table_counts["task_attachments"] = len(attachment_rows)
                subs = src.execute(f"SELECT * FROM kanban_notify_subs WHERE task_id IN ({placeholders})", ids).fetchall()
                # Notification endpoints are machine-local delivery state.
                table_counts["kanban_notify_subs"] = 0
                for task_id in ids:
                    kb._append_event(dst, task_id, "board_merged", {"source_board": source, "destination_board": destination})
            result_counts = {table: table_counts.get(table, 0) for table in _COUNTED_TABLES}
            migrated_ids = {row[0] for row in dst.execute(f"SELECT id FROM tasks WHERE id IN ({placeholders})", ids)}
            if migrated_ids != set(ids):
                raise RuntimeError("post-merge card identity verification failed")
            cleanup.pop_all()
            _remove_source_task_rows(source, ids)
            return {"source": source, "destination": destination, "tasks": len(ids),
                    "counts": result_counts, "backup": archive["archive"]}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _available_slug(preferred: str) -> str:
    """``preferred`` or the first free ``<preferred>-N``. ``default`` always
    exists, so a default-board export lands as ``default-2``."""
    if not kb.board_exists(preferred):
        return preferred
    # Leave headroom for the suffix inside the 64-char slug limit.
    stem = preferred[:58].rstrip("-_") or "board"
    n = 2
    while kb.board_exists(f"{stem}-{n}"):
        n += 1
    return f"{stem}-{n}"


def _read_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifest.json"
    if not path.exists():
        raise ValueError("archive is not a Hermes kanban board export (no manifest.json)")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"archive manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != ARCHIVE_FORMAT:
        raise ValueError(
            "archive is not a Hermes kanban board export "
            f"(format={manifest.get('format') if isinstance(manifest, dict) else None!r})"
        )
    version = manifest.get("format_version")
    if not isinstance(version, int) or version > ARCHIVE_FORMAT_VERSION:
        raise ValueError(
            f"archive format version {version!r} is newer than this Hermes "
            f"understands (max {ARCHIVE_FORMAT_VERSION}) — update Hermes and retry"
        )
    return manifest


def _read_board_metadata(path: Path) -> dict[str, Any]:
    """Read an archive's ``board.json``, tolerating a missing/broken file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _relocate_imported_rows(conn: sqlite3.Connection, slug: str) -> tuple[dict[str, int], list[str]]:
    """Re-anchor an imported board's rows to this machine; returns ``(stats, warnings)``.

    * Attachment rows are repointed at this board's tree; rows whose blob
      did not travel (``--no-attachments``) are dropped, since a dangling row
      breaks download in every UI.
    * Workspace paths are cleared. ``scratch`` regenerates on next claim;
      dispatchable ``dir``/``worktree`` tasks are parked in ``triage``,
      otherwise the dispatcher claims them, fails to build a workspace, and
      burns them into the failure breaker.
    * Runtime state is scrubbed again (untrusted input, one UPDATE).
    """
    warnings: list[str] = []
    now = int(time.time())
    attachments_dir = kb.attachments_root(slug)

    with kb.write_txn(conn):
        _scrub_local_state(conn)

        dropped = rehomed = 0
        for row in conn.execute("SELECT id, task_id, stored_path FROM task_attachments").fetchall():
            landed = attachments_dir / row["task_id"] / Path(row["stored_path"]).name
            if landed.is_file():
                conn.execute("UPDATE task_attachments SET stored_path = ? WHERE id = ?", (str(landed), row["id"]))
                rehomed += 1
            else:
                conn.execute("DELETE FROM task_attachments WHERE id = ?", (row["id"],))
                dropped += 1
        if dropped:
            warnings.append(f"{dropped} attachment record(s) dropped — the files were not in the archive")

        parked = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM tasks WHERE workspace_kind IN ('dir', 'worktree') "
                f"AND status IN ({_placeholders(_DISPATCHABLE_STATUSES)})",
                _DISPATCHABLE_STATUSES,
            ).fetchall()
        ]
        conn.execute("UPDATE tasks SET workspace_path = NULL, branch_name = NULL")
        if parked:
            conn.execute(f"UPDATE tasks SET status = 'triage' WHERE id IN ({_placeholders(parked)})", parked)
            warnings.append(
                f"{len(parked)} task(s) moved to triage — their workspace was a directory or git "
                f"worktree on the exporting machine and needs to be pointed somewhere on this one"
            )

        for row in conn.execute("SELECT id FROM tasks").fetchall():
            conn.execute(
                "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) "
                "VALUES (?, NULL, 'imported', ?, ?)",
                (row["id"], json.dumps({"board": slug, "parked": row["id"] in parked}, ensure_ascii=False), now),
            )

    return {"attachments": rehomed, "parked": len(parked)}, warnings


def import_board(
    archive_path: str,
    slug: Optional[str] = None,
    *,
    activate: bool = False,
) -> dict[str, Any]:
    """Import an archive as a NEW board (``slug`` overrides the archive's;
    either way it auto-suffixes if taken). Returns a summary dict."""
    archive = Path(archive_path).expanduser()
    if not archive.exists():
        raise FileNotFoundError(f"archive not found: {archive}")

    roots = archive_root_dirs(archive)
    if len(roots) != 1:
        raise ValueError("a kanban board archive must contain exactly one top-level directory")
    archive_root = roots.pop()

    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir)
        safe_extract_targz(archive, staging)
        extracted = staging / archive_root

        manifest = _read_manifest(extracted)
        staged_db = extracted / "kanban.db"
        if not staged_db.is_file():
            raise ValueError("archive is missing kanban.db")

        requested = kb._normalize_board_slug(slug or manifest.get("board") or archive_root)
        if not requested:
            raise ValueError(
                "cannot determine a board name from the archive — pass one "
                "explicitly with --as <slug>"
            )
        target = _available_slug(requested)

        staged_meta = _read_board_metadata(extracted / "board.json")

        board_root = kb.board_dir(target)
        board_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged_db), str(board_root / "kanban.db"))
        for tree in ("attachments", "logs"):
            src = extracted / tree
            if src.is_dir():
                shutil.move(str(src), str(board_root / tree))

    # Rewritten rather than moved across: the archive's copy names a slug
    # and a workdir that belong to the exporting machine.
    name = str(staged_meta.get("name") or manifest.get("board_name") or target)
    kb.write_board_metadata(
        target,
        name=name,
        description=str(staged_meta.get("description") or ""),
        icon=str(staged_meta.get("icon") or ""),
        color=str(staged_meta.get("color") or ""),
        archived=False,
    )
    # Bring the imported schema up to this install's version before the
    # relocation pass writes to it.
    kb.init_db(board=target)

    with kbc.connect_closing(board=target) as conn:
        stats, warnings = _relocate_imported_rows(conn, target)
        counts = _count_rows(conn)

    if activate:
        kb.set_current_board(target)

    return {
        "board": target,
        "requested_board": requested,
        "renamed": target != requested,
        "name": name,
        "path": str(kb.board_dir(target)),
        "db_path": str(kb.kanban_db_path(target)),
        "source": {
            "board": manifest.get("board"),
            "exported_at": manifest.get("exported_at"),
            "hermes_version": manifest.get("hermes_version"),
        },
        "counts": counts,
        "attachments_restored": stats["attachments"],
        "tasks_parked": stats["parked"],
        "warnings": warnings,
        "activated": bool(activate),
    }
