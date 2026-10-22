"""Operator-only reconciliation for one exact same-root legacy project duplicate."""

from contextlib import ExitStack, closing, contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from hermes_platform.host.facts import os_family

from hermes_cli import kanban_db as kb, projects_db
from hermes_cli.kanban_db_boards import (
    board_ownership_lock,
    board_project_ownership_lock,
)
from hermes_cli.kanban_db_workspace import _is_terminal_task, _path_key
from hermes_cli.profiles_lifecycle import profile_lifecycle_reservation
from hermes_cli.projects_adoption import (
    _FOLDER_COLUMNS,
    _PROJECT_COLUMNS,
    _confirm_identities,
    _identity,
    _insert_binding,
    _paths,
    _root_identity,
    _source_binding,
)

_TERMINAL_RUN_STATUSES = frozenset(
    {
        "blocked",
        "crashed",
        "done",
        "failed",
        "gave_up",
        "paused",
        "reclaimed",
        "released",
        "review",
        "scheduled",
        "stale",
        "timed_out",
        "todo",
        "triage",
    }
)
_REFERENCE_FIELDS = frozenset(
    {
        "id",
        "status",
        "project_id",
        "workspace_kind",
        "workspace_path",
        "branch_name",
        "current_run_id",
        "claim_lock",
        "claim_expires",
        "worker_pid",
        "run_count",
        "event_count",
    }
)


class _CommitOutcomeUncertain(RuntimeError):
    """A commit or its cleanup could not be confirmed; inspect the receipt."""


def _active_id_from_snapshot(snapshot):
    return next(
        (row["value"] for row in snapshot["project_meta"] if row["key"] == "active_id"),
        None,
    )


def _validate_expectation_shape(
    *,
    expected_project_id,
    expected_root,
    expected_board,
    legacy_project_id,
    expected_legacy_project,
    expected_legacy_folders,
    expected_active_id,
    expected_boards,
    expected_references,
):
    """Refuse malformed operator expectations before any receipt or lock work."""
    if (
        not isinstance(expected_project_id, str)
        or not expected_project_id
        or not isinstance(legacy_project_id, str)
        or not legacy_project_id
        or legacy_project_id == expected_project_id
        or not isinstance(expected_root, (str, os.PathLike))
        or not isinstance(expected_board, str)
        or kb._normalize_board_slug(expected_board) != expected_board
        or not isinstance(expected_legacy_project, dict)
        or set(expected_legacy_project) != _PROJECT_COLUMNS
        or not isinstance(expected_legacy_folders, list)
        or not isinstance(expected_active_id, (str, type(None)))
    ):
        raise ValueError("canonicalization expectations have an invalid identity shape")
    if (
        not isinstance(expected_boards, (list, tuple))
        or not all(
            isinstance(board, str) and kb._normalize_board_slug(board) == board
            for board in expected_boards
        )
        or len(set(expected_boards)) != len(expected_boards)
        or expected_board not in expected_boards
        or not isinstance(expected_references, dict)
        or set(expected_references) != set(expected_boards)
    ):
        raise ValueError("expected board/reference inventory is incomplete")
    for folder in expected_legacy_folders:
        if not isinstance(folder, dict) or set(folder) != _FOLDER_COLUMNS:
            raise ValueError("legacy folder expectation has an invalid row shape")
    for board, references in expected_references.items():
        if not isinstance(references, list):
            raise ValueError("each board reference expectation must be a list")
        seen = set()
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != _REFERENCE_FIELDS:
                raise ValueError("task reference expectation has an invalid row shape")
            task_id = reference["id"]
            if not isinstance(task_id, str) or not task_id or task_id in seen:
                raise ValueError("task reference expectation has an invalid identity")
            seen.add(task_id)
            if (
                not isinstance(reference["status"], str)
                or not isinstance(reference["project_id"], str)
                or any(
                    reference[key] is not None and not isinstance(reference[key], str)
                    for key in (
                        "workspace_kind",
                        "workspace_path",
                        "branch_name",
                        "current_run_id",
                        "claim_lock",
                    )
                )
                or type(reference["claim_expires"]) not in (int, type(None))
                or type(reference["worker_pid"]) not in (int, type(None))
                or type(reference["run_count"]) is not int
                or reference["run_count"] < 0
                or type(reference["event_count"]) is not int
                or reference["event_count"] < 0
            ):
                raise ValueError(
                    f"task reference expectation for board {board!r} has invalid field types"
                )


def _registry_snapshot(conn):
    """Return every row in the existing project registry without schema repair."""
    expected = {
        "projects": _PROJECT_COLUMNS,
        "project_folders": _FOLDER_COLUMNS,
        "project_meta": {"key", "value"},
        "discovered_repos": {"root", "label", "last_seen"},
    }
    snapshot = {}
    for table, fields in expected.items():
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if columns != fields:
            raise ValueError("canonicalization refuses unsupported registry schema")
        snapshot[table] = [
            dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
        ]
    return snapshot


def _registry_read(path):
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _registry_write(path):
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _board_database_absent(slug):
    """Accept proved absence only, never orphaned SQLite recovery state or aliases."""
    path = kb.kanban_db_path(slug)
    if path.resolve() != path:
        raise ValueError("board database path is not canonical")
    try:
        path.lstat()
    except FileNotFoundError:
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(str(path) + suffix).lstat()
            except FileNotFoundError:
                continue
            raise ValueError("missing board database has SQLite recovery state")
        return True
    return False


def _board_connection(slug, *, readonly):
    path = kb.kanban_db_path(slug)
    if _board_database_absent(slug):
        return None
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise ValueError(f"board {slug!r} has no canonical existing database")
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(path.as_uri() + f"?mode={mode}", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _reference_snapshot(conn, legacy_id, expected, board):
    from hermes_cli import kanban_transfer

    if conn is None:
        if expected[board] or not _board_database_absent(board):
            raise ValueError("absent board differs from reviewed empty references")
        return {"tasks": [], "related": {}, "summaries": [], "database_absent": True}
    task_ids = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM tasks WHERE project_id=? ORDER BY id", (legacy_id,)
        )
    ]
    expected_ids = sorted(item["id"] for item in expected.get(board, []))
    if task_ids != expected_ids:
        raise ValueError("project references changed or contain an unknown task")
    selected = kanban_transfer._selected_task_rows(conn, task_ids)
    summaries = []
    for task_id in task_ids:
        task = next((row for row in selected["tasks"] if row["id"] == task_id), None)
        if task is None or task["project_id"] != legacy_id:
            raise ValueError("task reference changed during canonicalization")
        runs = [row for row in selected["task_runs"] if row["task_id"] == task_id]
        events = [row for row in selected["task_events"] if row["task_id"] == task_id]
        if (
            not _is_terminal_task(conn, task_id)
            or task["current_run_id"] is not None
            or task["claim_lock"] is not None
            or task["claim_expires"] is not None
            or task["worker_pid"] is not None
            or any(
                run["status"] not in _TERMINAL_RUN_STATUSES or run["ended_at"] is None
                for run in runs
            )
        ):
            raise ValueError(
                "canonicalization requires terminal unclaimed task references"
            )
        summary = {
            key: task[key]
            for key in (
                "id",
                "status",
                "project_id",
                "workspace_kind",
                "workspace_path",
                "branch_name",
                "current_run_id",
                "claim_lock",
                "claim_expires",
                "worker_pid",
            )
        }
        summary.update(run_count=len(runs), event_count=len(events))
        if summary != next(
            (item for item in expected[board] if item["id"] == task_id), None
        ):
            raise ValueError(
                "terminal task reference differs from the approved snapshot"
            )
        summaries.append(summary)
    return {
        "tasks": selected["tasks"],
        "related": {key: value for key, value in selected.items() if key != "tasks"},
        "summaries": summaries,
    }


def _board_inventory(expected_boards):
    rows = kb.list_boards(include_archived=True)
    archive_root = kb.boards_root() / "_archived"
    if (
        any(row.get("archived") for row in rows)
        or archive_root.is_symlink()
        or (archive_root.exists() and any(archive_root.iterdir()))
    ):
        raise ValueError(
            "archived board history must be reviewed before canonicalization"
        )
    actual = tuple(sorted(row["slug"] for row in rows))
    if actual != tuple(sorted(expected_boards)):
        raise ValueError("board inventory differs from the reviewed snapshot")
    return {row["slug"]: row for row in rows}


def _publish_private_receipt(path, document):
    """Durably publish an owner-only receipt without replacing any existing path."""
    path = Path(path)
    if (
        not path.is_absolute()
        or path.resolve(strict=False) != path
        or not path.parent.is_dir()
        or path.exists()
        or path.is_symlink()
    ):
        raise ValueError("a new canonical private recovery receipt path is required")
    if any(parent.is_symlink() for parent in (path.parent, *path.parent.parents)):
        raise ValueError("recovery receipt parent must not traverse a symlink")
    payload = (
        json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode()
    with tempfile.TemporaryDirectory(
        prefix=".project-reconcile-", dir=path.parent
    ) as staging:
        temporary = Path(staging) / "receipt.json"
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
            # Windows cannot open directories with os.open; retain file fsync and
            # no-overwrite publication there, without suppressing POSIX sync errors.
            if os_family() != "win32":
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except BaseException:
            raise
    return hashlib.sha256(payload).hexdigest()


def _retired_projection(project):
    suffix = f"-retired-{project['id']}"
    slug = f"{project['slug'][: 64 - len(suffix)].rstrip('-_')}{suffix}"
    return {
        **project,
        "slug": slug,
        "board_slug": None,
        "primary_path": None,
        "archived": 1,
    }


def _validate_duplicate_rows(
    source_conn,
    source_before,
    target_before,
    expected_project_id,
    root,
    expected_board,
    legacy_project_id,
    expected_legacy_project,
    expected_legacy_folders,
    expected_active_id,
):
    source_binding = _source_binding(
        source_conn, expected_project_id, root, expected_board
    )
    source_projects = [
        row for row in source_before["projects"] if row["id"] == expected_project_id
    ]
    if len(source_projects) != 1:
        raise ValueError("canonical source project is not unique")
    project = source_projects[0]
    _, folder = source_binding
    if Path(project["primary_path"]) != root:
        raise ValueError("canonical source root differs from the explicit expectation")
    legacy_rows = target_before["projects"]
    if len(legacy_rows) != 1 or legacy_rows[0] != expected_legacy_project:
        raise ValueError("target must contain only the exact reviewed legacy project")
    if (
        expected_legacy_project.get("id") != legacy_project_id
        or expected_legacy_project.get("slug") != project["slug"]
        or expected_legacy_project.get("primary_path") != str(root)
        or expected_legacy_project.get("board_slug") is not None
        or expected_legacy_project.get("archived") != 0
    ):
        raise ValueError(
            "legacy project is not the exact unarchived same-root duplicate"
        )
    if (
        target_before["project_folders"] != expected_legacy_folders
        or len(expected_legacy_folders) != 1
    ):
        raise ValueError("target folders differ from the reviewed duplicate projection")
    legacy_folder = expected_legacy_folders[0]
    if (
        legacy_folder.get("project_id") != legacy_project_id
        or legacy_folder.get("path") != str(root)
        or legacy_folder.get("is_primary") != 1
    ):
        raise ValueError("legacy primary folder is not the reviewed canonical root")
    if _active_id_from_snapshot(target_before) != expected_active_id:
        raise ValueError("target active selection differs from the reviewed snapshot")
    return project, folder, source_binding


def _capture_board_state(
    expected_boards,
    expected_board,
    expected_project_id,
    legacy_project_id,
    expected_references,
):
    metadata_before = {}
    reference_before = {}
    for board in sorted(expected_boards):
        metadata = kb.read_board_metadata(board)
        if (
            board == expected_board
            and metadata.get("project_id") != expected_project_id
        ):
            raise ValueError("canonical board ownership changed")
        if board != expected_board and metadata.get("project_id") == legacy_project_id:
            raise ValueError("legacy project is still claimed by another board")
        metadata_before[board] = metadata
        connection = _board_connection(board, readonly=True)
        with closing(connection) if connection is not None else nullcontext() as conn:
            reference_before[board] = _reference_snapshot(
                conn, legacy_project_id, expected_references, board
            )
    return metadata_before, reference_before


def _capture_prestate(
    source,
    target,
    expected_project_id,
    root,
    expected_board,
    legacy_project_id,
    expected_legacy_project,
    expected_legacy_folders,
    expected_active_id,
    expected_boards,
    expected_references,
):
    with (
        closing(_registry_read(source)) as source_conn,
        closing(_registry_read(target)) as target_conn,
    ):
        source_before = _registry_snapshot(source_conn)
        target_before = _registry_snapshot(target_conn)
        project, folder, source_binding = _validate_duplicate_rows(
            source_conn,
            source_before,
            target_before,
            expected_project_id,
            root,
            expected_board,
            legacy_project_id,
            expected_legacy_project,
            expected_legacy_folders,
            expected_active_id,
        )
    metadata_before, reference_before = _capture_board_state(
        expected_boards,
        expected_board,
        expected_project_id,
        legacy_project_id,
        expected_references,
    )
    return {
        "source_before": source_before,
        "target_before": target_before,
        "source_binding": source_binding,
        "project": project,
        "folder": folder,
        "metadata_before": metadata_before,
        "reference_before": reference_before,
    }


def _receipt_document(
    state,
    source,
    target,
    target_profile,
    expected_project_id,
    legacy_project_id,
    root,
    expected_board,
    registry_identities,
    root_identity,
):
    return {
        "document_type": "hermes.project-canonicalization-recovery",
        "schema_version": 1,
        "target_profile": target_profile,
        "source_registry": str(source),
        "target_registry": str(target),
        "canonical_project_id": expected_project_id,
        "legacy_project_id": legacy_project_id,
        "root": str(root),
        "board": expected_board,
        "registry_file_identities": {
            str(path): list(identity) for path, identity in registry_identities.items()
        },
        "root_identity": [
            list(root_identity[0]),
            str(root_identity[1]),
            list(root_identity[2]),
        ],
        "source_before": state["source_before"],
        "target_before": state["target_before"],
        "board_metadata_before": state["metadata_before"],
        "references": state["reference_before"],
    }


@contextmanager
def _manual_write_txn(conn):
    """Allow the target registry commit to occur while later-acquired board locks remain held."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except _CommitOutcomeUncertain:
        # Do not retry cleanup: the commit helper already tried, and repeating
        # a failed rollback could hide its explicit uncertain-outcome signal.
        raise
    except sqlite3.Error as exc:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error as rollback_exc:
                error = _CommitOutcomeUncertain(
                    "transaction rollback outcome is uncertain; inspect the private receipt before retry"
                )
                error.add_note(f"Original transaction error: {type(exc).__name__}")
                error.add_note(f"Rollback cleanup error: {type(rollback_exc).__name__}")
                raise error from exc
        raise
    else:
        if conn.in_transaction:
            conn.execute("COMMIT")


def _commit_target_registry(conn):
    """Commit the target last, reporting ambiguous SQLite commit outcomes honestly."""
    try:
        conn.execute("COMMIT")
    except BaseException as exc:
        if not conn.in_transaction:
            raise _CommitOutcomeUncertain(
                "canonicalization target commit outcome is uncertain; inspect the private receipt before retry"
            ) from exc
        try:
            conn.execute("ROLLBACK")
        except BaseException as rollback_exc:
            raise _CommitOutcomeUncertain(
                "canonicalization target commit and rollback outcomes are uncertain; inspect the private receipt before retry"
            ) from rollback_exc
        raise RuntimeError(
            "canonicalization target commit failed and was rolled back; retain the private receipt"
        ) from exc


@contextmanager
def _reconciliation_transactions(source, target, expected_boards):
    """Acquire metadata, absent-DB init reservations, registries, then board DBs."""
    from hermes_cli.kanban_db_connect import _cross_process_init_lock

    with ExitStack() as stack:
        for board in sorted(expected_boards):
            stack.enter_context(board_ownership_lock(board))
        # connect() holds this same reservation before opening a missing DB.
        # Acquire before registry writers: initialization may read project state.
        for board in sorted(expected_boards):
            if _board_database_absent(board):
                stack.enter_context(_cross_process_init_lock(kb.kanban_db_path(board), strict=True))
        registries = {}
        for path in sorted((source, target), key=_path_key):
            conn = stack.enter_context(closing(_registry_write(path)))
            stack.enter_context(_manual_write_txn(conn))
            registries[path] = conn
        boards = {}
        for board in sorted(expected_boards):
            connection = _board_connection(board, readonly=False)
            conn = stack.enter_context(
                closing(connection) if connection is not None else nullcontext()
            )
            if conn is not None:
                stack.enter_context(_manual_write_txn(conn))
            boards[board] = conn
        yield registries, boards


def _verify_commit_snapshot(
    source,
    target,
    target_profile,
    root,
    root_identity,
    registry_identities,
    state,
    expected_boards,
    legacy_project_id,
    expected_references,
    inventory,
    registries,
    boards,
):
    if (
        _paths(source, target, target_profile) != (source, target)
        or _identity(source) != registry_identities[source]
        or _identity(target) != registry_identities[target]
    ):
        raise ValueError("registry identity changed after recovery receipt")
    _confirm_identities(registry_identities, root, root_identity)
    if (
        _registry_snapshot(registries[source]) != state["source_before"]
        or _registry_snapshot(registries[target]) != state["target_before"]
    ):
        raise ValueError("registry changed after recovery receipt")
    if (
        _source_binding(
            registries[source],
            state["project"]["id"],
            root,
            state["project"]["board_slug"],
        )
        != state["source_binding"]
    ):
        raise ValueError("canonical source binding changed after recovery receipt")
    if _board_inventory(expected_boards) != inventory:
        raise ValueError("board inventory changed after recovery receipt")
    for board, conn in boards.items():
        if kb.read_board_metadata(board) != state["metadata_before"][board]:
            raise ValueError("board metadata changed after recovery receipt")
        if (
            _reference_snapshot(conn, legacy_project_id, expected_references, board)
            != state["reference_before"][board]
        ):
            raise ValueError("task references/history changed after recovery receipt")


def _apply_target_projection(
    registries,
    source,
    target,
    state,
    legacy_project_id,
    expected_project_id,
    root,
    expected_board,
    registry_identities,
    root_identity,
):
    target_conn = registries[target]
    retired = _retired_projection(
        next(
            row
            for row in state["target_before"]["projects"]
            if row["id"] == legacy_project_id
        )
    )
    cur = target_conn.execute(
        "UPDATE projects SET slug=?, board_slug=NULL, primary_path=NULL, archived=1 WHERE id=?",
        (retired["slug"], legacy_project_id),
    )
    if cur.rowcount != 1:
        raise ValueError("legacy duplicate changed before retirement")
    target_conn.execute(
        "DELETE FROM project_folders WHERE project_id=?", (legacy_project_id,)
    )
    _insert_binding(target_conn, state["project"], state["folder"])
    if projects_db.get_active_id(target_conn) == legacy_project_id:
        _upsert_active(target_conn, expected_project_id)
    _verify_target_projection(
        target_conn, state, retired, legacy_project_id, expected_project_id
    )
    _confirm_identities(registry_identities, root, root_identity)
    if (
        _source_binding(registries[source], expected_project_id, root, expected_board)
        != state["source_binding"]
    ):
        raise ValueError("canonical source changed during target commit")


def _verify_target_projection(target_conn, state, retired, legacy_id, canonical_id):
    expected_projects = [retired, state["project"]]
    actual_projects = [
        dict(row) for row in target_conn.execute("SELECT * FROM projects ORDER BY id")
    ]
    if sorted(actual_projects, key=lambda row: row["id"]) != sorted(
        expected_projects, key=lambda row: row["id"]
    ):
        raise ValueError("target project registry failed exact postcondition")
    folders = [
        dict(row) for row in target_conn.execute("SELECT * FROM project_folders")
    ]
    if folders != [state["folder"]]:
        raise ValueError("target project folders failed exact postcondition")
    meta = [
        dict(row)
        for row in target_conn.execute("SELECT * FROM project_meta ORDER BY key")
    ]
    if meta != _expected_meta_after(
        state["target_before"]["project_meta"], legacy_id, canonical_id
    ):
        raise ValueError("target active/unrelated metadata changed unexpectedly")
    discovered = [
        dict(row)
        for row in target_conn.execute("SELECT * FROM discovered_repos ORDER BY rowid")
    ]
    if discovered != state["target_before"]["discovered_repos"]:
        raise ValueError("target repository discovery cache changed unexpectedly")


def _commit_reconciliation(
    source,
    target,
    target_profile,
    root,
    root_identity,
    registry_identities,
    state,
    expected_boards,
    legacy_project_id,
    expected_project_id,
    expected_board,
    expected_references,
    inventory,
):
    target_committed = False
    try:
        with _reconciliation_transactions(source, target, expected_boards) as (
            registries,
            boards,
        ):
            _verify_commit_snapshot(
                source,
                target,
                target_profile,
                root,
                root_identity,
                registry_identities,
                state,
                expected_boards,
                legacy_project_id,
                expected_references,
                inventory,
                registries,
                boards,
            )
            _apply_target_projection(
                registries,
                source,
                target,
                state,
                legacy_project_id,
                expected_project_id,
                root,
                expected_board,
                registry_identities,
                root_identity,
            )
            # Keep board writer transactions and ownership locks through the
            # linearization point. A history writer can proceed only afterward.
            registries[source].execute("COMMIT")
            _commit_target_registry(registries[target])
            target_committed = True
    except BaseException as exc:
        if target_committed:
            raise RuntimeError(
                "canonicalization committed but history-fence release failed; inspect the private receipt before retry"
            ) from exc
        raise


def retire_exact_duplicate_and_adopt(
    *,
    source_registry,
    target_registry,
    target_profile,
    expected_project_id,
    expected_root,
    expected_board,
    legacy_project_id,
    expected_legacy_project,
    expected_legacy_folders,
    expected_active_id,
    expected_boards,
    expected_references,
    backup_path,
):
    """Retire one reviewed duplicate while retaining its exact historical identity.

    The root-operator CLI consumer and task/delegation guard live in
    ``projects_cmd``. This data-repair operation grants no execution, REST,
    provider, credential, or model/RPC capability. A private recovery receipt
    precedes registry writes; cooperative source/profile/board writers are
    revalidated under their existing locks before the target commit.
    """
    _validate_expectation_shape(
        expected_project_id=expected_project_id,
        expected_root=expected_root,
        expected_board=expected_board,
        legacy_project_id=legacy_project_id,
        expected_legacy_project=expected_legacy_project,
        expected_legacy_folders=expected_legacy_folders,
        expected_active_id=expected_active_id,
        expected_boards=expected_boards,
        expected_references=expected_references,
    )
    if not backup_path:
        raise ValueError("canonicalization requires a private recovery receipt")
    source, target = _paths(source_registry, target_registry, target_profile)
    with profile_lifecycle_reservation(target.parent):
        return _retire_reserved(
            source=source,
            target=target,
            target_profile=target_profile,
            expected_project_id=expected_project_id,
            expected_root=expected_root,
            expected_board=expected_board,
            legacy_project_id=legacy_project_id,
            expected_legacy_project=expected_legacy_project,
            expected_legacy_folders=expected_legacy_folders,
            expected_active_id=expected_active_id,
            expected_boards=expected_boards,
            expected_references=expected_references,
            backup_path=backup_path,
        )


def _retire_reserved(
    *,
    source,
    target,
    target_profile,
    expected_project_id,
    expected_root,
    expected_board,
    legacy_project_id,
    expected_legacy_project,
    expected_legacy_folders,
    expected_active_id,
    expected_boards,
    expected_references,
    backup_path,
):
    # Hold the stable catalog reservation from the complete inventory check
    # through the target registry linearization point. Per-slug locks alone do
    # not coordinate a concurrent publisher that creates a new owner-bearing slug.
    with board_project_ownership_lock(expected_board):
        if _paths(source, target, target_profile) != (source, target):
            raise ValueError("registry path changed")
        root = Path(expected_root)
        root_identity = _root_identity(root)
        registry_identities = {path: _identity(path) for path in (source, target)}
        inventory = _board_inventory(expected_boards)
        with ExitStack() as locks:
            for board in sorted(expected_boards):
                locks.enter_context(board_ownership_lock(board))
            if _board_inventory(expected_boards) != inventory:
                raise ValueError(
                    "board metadata changed while ownership locks were acquired"
                )
            state = _capture_prestate(
                source,
                target,
                expected_project_id,
                root,
                expected_board,
                legacy_project_id,
                expected_legacy_project,
                expected_legacy_folders,
                expected_active_id,
                expected_boards,
                expected_references,
            )
        receipt = _receipt_document(
            state,
            source,
            target,
            target_profile,
            expected_project_id,
            legacy_project_id,
            root,
            expected_board,
            registry_identities,
            root_identity,
        )
        receipt_sha256 = _publish_private_receipt(backup_path, receipt)
        _commit_reconciliation(
            source,
            target,
            target_profile,
            root,
            root_identity,
            registry_identities,
            state,
            expected_boards,
            legacy_project_id,
            expected_project_id,
            expected_board,
            expected_references,
            inventory,
        )
        try:
            _paths(source, target, target_profile)
        except ValueError as exc:
            raise ValueError(
                "canonicalization committed but profile identity changed; inspect receipt before retry"
            ) from exc
        return {
            "status": "canonicalized",
            "project_id": expected_project_id,
            "retired_project_id": legacy_project_id,
            "backup": str(backup_path),
            "backup_sha256": receipt_sha256,
            "references_preserved": sum(
                len(value["summaries"]) for value in state["reference_before"].values()
            ),
        }


def _upsert_active(conn, project_id):
    conn.execute(
        "INSERT INTO project_meta (key, value) VALUES ('active_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (project_id,),
    )


def _expected_meta_after(before, legacy_id, canonical_id):
    rows = []
    for row in before:
        if row["key"] == "active_id" and row["value"] == legacy_id:
            rows.append({**row, "value": canonical_id})
        else:
            rows.append(row)
    return sorted(rows, key=lambda item: item["key"])
