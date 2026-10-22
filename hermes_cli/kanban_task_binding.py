"""Explicit, non-dispatching correction of a Triage card's repository binding.

Old materialized workspaces are never moved or cleaned. A correction may leave an
unmaterialized destination only when the old workspace is also unmaterialized.
Otherwise an independently provisioned, verified task worktree is required first.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import os
import subprocess
from pathlib import Path
import tempfile

from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, projects_db
from hermes_cli.kanban_db_boards import board_ownership_lock
from hermes_cli import kanban_db_workspace as workspace
from hermes_cli.kanban_worktree_policy import project_worktree_path
from hermes_cli.sqlite_util import write_txn

# A correction proves physical repository/ref ownership, never a caller-selected
# Git namespace. Refuse explicit overrides rather than silently changing the
# operator's intended context; authentication and ordinary discovery stay intact.
_GIT_NAMESPACE_OVERRIDES = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE",
    "GIT_GRAFT_FILE", "GIT_REPLACE_REF_BASE",
)

_BINDING_FIELDS = ("project_id", "workspace_kind", "workspace_path", "branch_name",
                   "workspace_base_ref", "workspace_base_sha")


@dataclass(frozen=True)
class BindingExpectation:
    """Exact old fields and registered root, supplied by the reviewed caller."""
    values: tuple
    project_root: str
    board_project_id: str

    @classmethod
    def from_task(cls, task, *, project_root: str, board_project_id=None):
        return cls(tuple(task[key] for key in _BINDING_FIELDS), project_root,
                   board_project_id or task["project_id"])


@dataclass(frozen=True)
class BindingProjection:
    """Trusted internal projection, not an authorization capability.

    The concrete type restricts changed columns; isinstance is not caller admission.
    The correction API owns canonical identity validation and ownership reservations.
    """
    original: dict
    project_id: str
    workspace_path: str
    branch_name: str | None
    payload: dict
    target_project: projects_db.Project

    def apply(self, task):
        if dict(task) != self.original:
            raise ValueError("source card changed before binding correction")
        return {**task, "project_id": self.project_id, "workspace_kind": "worktree",
                "workspace_path": self.workspace_path, "branch_name": self.branch_name,
                "workspace_base_ref": None, "workspace_base_sha": None}

    def append_event(self, conn, task_id):
        kb._append_event(conn, task_id, "project_binding_corrected", self.payload)

    def verify_destination(self):
        path, branch = _destination(self.original, self.target_project)
        if str(path) != self.workspace_path or branch != self.branch_name:
            raise ValueError("destination workspace policy changed")

    def verify_workspace_users(self, src, dst, *, retained_copy):
        """Exclude only a counterpart already verified in the locked destination."""
        from hermes_cli.kanban_db_connect import _main_db_file

        task_id = self.original["id"]
        key = workspace._path_key(Path(self.workspace_path).resolve())
        if (workspace._conn_uses_path(src, task_id, key)
                or workspace._conn_uses_path(dst, task_id, key, exclude_task_id=retained_copy)):
            raise ValueError("destination workspace belongs to another card")
        destination_file = Path(_main_db_file(dst)).resolve()
        for db_file in workspace._sibling_board_db_files(src):
            if db_file.resolve() != destination_file and workspace._other_board_uses_path(db_file, task_id, key):
                raise ValueError("destination workspace belongs to another board's card")


@dataclass(frozen=True)
class DirectoryBindingProjection(BindingProjection):
    """Fixed-kind local-only correction; old Git bindings remain in recovery evidence."""

    directory_identity: tuple[int, int]

    def apply(self, task):
        return {**super().apply(task), "workspace_kind": "dir"}

    def verify_destination(self):
        path = _directory_destination(
            self.original, self.target_project, self.workspace_path
        )
        if (path.stat().st_dev, path.stat().st_ino) != self.directory_identity:
            raise ValueError("destination directory identity changed")


def _branch_exists(repo_root, branch_name):
    """Only a successful local probe can establish presence or absence."""
    try:
        result = workspace._git(repo_root, "show-ref", "--verify", "--quiet",
                                f"refs/heads/{branch_name}", timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("branch absence could not be established") from None
    if result.returncode == 0:
        return True
    if result.returncode == 1 and not result.stdout and not result.stderr:
        return False
    raise ValueError("branch absence could not be established")


def _outside_repository(path):
    """Prove physical repository isolation, independent of inherited discovery bindings."""
    from hermes_cli._subprocess_compat import bounded_probe_run, noninteractive_repo_git_env

    base = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES", "GIT_OBJECT_DIRECTORY"):
        base.pop(key, None)
    base.update(GIT_DISCOVERY_ACROSS_FILESYSTEM="1", LC_ALL="C")
    try:
        env = noninteractive_repo_git_env(path, base=base)
        result = None if env is None else bounded_probe_run(
            ["git", "-C", str(path), "rev-parse", "--absolute-git-dir"],
            timeout=20, errors="replace", env=env)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ValueError("directory repository isolation could not be established") from None
    if result is not None:
        if result.returncode == 0:
            return False  # Includes bare repositories, unlike --show-toplevel.
        if (result.returncode == 128 and not result.stdout and result.stderr.strip()
                == "fatal: not a git repository (or any of the parent directories): .git"):
            return True
    raise ValueError("directory repository isolation could not be established")


def _directory_destination(task, project, directory):
    path = Path(directory).expanduser()
    root = Path(project.primary_path).resolve()
    if (
        not path.is_absolute()
        or not path.is_dir()
        or path.resolve() != path
        or path.is_relative_to(root)
        or root.is_relative_to(path)
        or not _outside_repository(path)
    ):
        raise ValueError(
            "destination directory must be existing, isolated, canonical and outside repositories"
        )
    old = Path(task["workspace_path"]) if task.get("workspace_path") else None
    if (
        task.get("workspace_kind") != "worktree"
        or old is None
        or not old.is_absolute()
        or not task.get("branch_name")
    ):
        raise ValueError("directory correction requires an exact old worktree binding")
    if (
        old.exists()
        or old.is_symlink()
        or _branch_exists(Path(project.primary_path), task["branch_name"])
    ):
        raise ValueError(
            "materialized old workspace/branch must remain under its existing ownership"
        )
    return path


def _correction_scope(old, target, source_board, destination, expected, directory):
    if directory is not None:
        if (
            old.id != target.id
            or source_board != destination
            or old.board_slug != destination
            or expected.board_project_id != target.id
        ):
            raise ValueError(
                "directory correction requires the same canonical project and board"
            )
    elif old.id == target.id or workspace._path_key(
        Path(old.primary_path).resolve()
    ) == workspace._path_key(Path(target.primary_path).resolve()):
        raise ValueError("correction requires distinct registered repositories")


def _binding_projection(
    conn, task, old, target, source_board, destination, reason, directory
):
    if directory is not None:
        if task.get("claim_expires") is not None:
            raise ValueError("directory correction refuses incomplete claim identity")
        from hermes_cli.kanban_pr_acceptance import validate_contract

        if validate_contract(task.get("completion_contract")) != "local-only":
            raise ValueError(
                "directory correction requires a local-only completion contract"
            )
        if conn.execute(
            "SELECT 1 FROM task_runs WHERE task_id=? AND status IN ('claimed','unknown')",
            (task["id"],),
        ).fetchone():
            raise ValueError("directory correction refuses unsettled runs")
        path, branch = _directory_destination(task, target, directory), None
    else:
        path, branch = _destination(task, target)
    payload = {
        "reason": reason,
        "source_board": source_board,
        "destination_board": destination,
        "old_binding": {key: task[key] for key in _BINDING_FIELDS},
        "old_project_root": old.primary_path,
        "new_project_root": target.primary_path,
        "new_project_id": target.id,
        "new_workspace_path": str(path),
        "new_branch_name": branch,
    }
    args = (task, target.id, str(path), branch, payload, target)
    if directory is not None:
        payload["new_workspace_kind"] = "dir"
        return DirectoryBindingProjection(
            *args, (path.stat().st_dev, path.stat().st_ino)
        )
    return BindingProjection(*args)


def _verify_workspace_availability(source, destination, snapshot, projection):
    from hermes_cli import kanban_transfer as transfer

    task_id = projection.original["id"]
    with kbc.connect_closing(board=source) as src, kbc.connect_closing(board=destination) as dst, ExitStack() as locks:
        for _, conn in sorted(((source, src), (destination, dst)), key=lambda item: item[0]):
            locks.enter_context(kb.write_txn(conn))
        rows = transfer._selected_task_rows(src, [task_id])
        if rows != snapshot:
            raise ValueError("source card or history changed before workspace verification")
        copied = transfer._selected_task_rows(dst, [task_id])
        if copied["tasks"]:
            transfer._verify_task_copy(source, destination, rows, copied, [task_id], binding=projection)
        projection.verify_workspace_users(src, dst, retained_copy=bool(copied["tasks"]))


def _private_recovery_snapshot(source, recovery):
    """Publish a complete owner-only snapshot atomically, without replacing a path."""
    from hermes_cli import kanban_transfer as transfer

    with tempfile.TemporaryDirectory(prefix=".binding-recovery-", dir=recovery.parent) as staging:
        snapshot = Path(staging) / "source.sqlite3"
        snapshot_fd = os.open(snapshot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(snapshot_fd)
        transfer._snapshot_db(source, snapshot)
        # Hard-link publication refuses an existing file or symlink atomically.
        # The private staging directory prevents replacing SQLite's open target.
        os.link(snapshot, recovery)


def _registered_project(registry, identity, board, root):
    project = projects_db.get_project(registry, identity)
    if project is None or project.id != identity or project.archived:
        raise ValueError("binding requires a current canonical registered project ID")
    if project.board_slug != board or not project.primary_path:
        raise ValueError("registered project board/root binding changed")
    expected = Path(root).expanduser()
    primary = Path(project.primary_path)
    if not expected.is_absolute() or workspace._path_key(expected.resolve()) != workspace._path_key(primary.resolve()):
        raise ValueError("registered project root changed")
    metadata = kb.read_board_metadata(board)
    if not kb.board_exists(board) or metadata.get("archived") or metadata.get("project_id") != identity:
        raise ValueError("board ownership does not match registered project")
    if workspace._git_toplevel(primary) != primary.resolve():
        raise ValueError("registered project root is not a repository root")
    return project


def _safe_task(conn, task_id, expected):
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None or tuple(row[key] for key in _BINDING_FIELDS) != expected.values:
        raise ValueError("expected task binding changed")
    if row["status"] != "triage" or any(row[key] for key in ("claim_lock", "current_run_id", "worker_pid")):
        raise ValueError("binding correction requires unclaimed Triage with no current worker/run")
    if conn.execute("SELECT 1 FROM task_runs WHERE task_id=? AND (status='running' OR ended_at IS NULL)", (task_id,)).fetchone():
        raise ValueError("binding correction refuses unfinished runs")
    if conn.execute("SELECT 1 FROM task_links WHERE parent_id=? OR child_id=?", (task_id, task_id)).fetchone():
        raise ValueError("binding correction refuses dependency crossings")
    return dict(row)


def _destination(task, project):
    root = Path(project.primary_path).resolve()
    target = project_worktree_path(root, task["id"])
    branch = projects_db.branch_name_for(project, task["id"], title=task["title"])
    if target.is_symlink():
        raise ValueError("destination workspace must not be a symlink")
    if target.exists():
        if (not workspace._is_linked_worktree_checkout(target)
                or workspace._git_toplevel(target) != target.resolve()
                or workspace._git_common_dir(target) != workspace._git_common_dir(root)
                or workspace._git_current_branch(target) != branch):
            raise ValueError("destination is not the owned task worktree")
    elif (task["workspace_path"] and (Path(task["workspace_path"]).exists()
                                     or Path(task["workspace_path"]).is_symlink())) or _branch_exists(root, branch):
        raise ValueError("materialized old workspace/branch requires an owned destination first")
    return target, branch


def correct_task_binding(*, source_board, task_id, target_project_id, expected: BindingExpectation,
                         target_root, reason, backup_path, target_directory=None):
    """Preserve a card and its holds; never claim, promote, materialize, or clean it.

    Metadata ownership locks precede the registry writer reservation, matching audit
    enqueue. Board DB writers follow in sorted order through verified transfer.
    The backup and old-binding event are durable evidence, not a workspace GC lease.
    ``target_directory`` is an operator-only same-project local-only correction to
    an existing isolated directory; it never releases a card or changes creation policy.
    Explicit Git namespace overrides must be unset before either correction mode.
    """
    # Presence matters: Git also interprets explicitly empty namespace values.
    if any(key in os.environ for key in _GIT_NAMESPACE_OVERRIDES):
        raise ValueError("binding correction refuses inherited Git namespace")
    from hermes_cli import kanban_transfer as transfer

    if not reason.strip() or not backup_path or not expected.values[0]:
        raise ValueError("binding correction requires expected identity, reason, and recovery backup")
    if not projects_db.projects_db_path().is_file():
        raise ValueError("binding correction requires the existing calling-profile registry")
    source_board = kb._normalize_board_slug(source_board)
    with projects_db.connect_closing() as registry:
        target = projects_db.get_project(registry, target_project_id)
        old_record = projects_db.get_project(registry, expected.values[0])
    if target is None or not target.board_slug or old_record is None or not old_record.board_slug:
        raise ValueError("both projects require canonical registered boards")
    destination = target.board_slug
    old_board = old_record.board_slug
    with ExitStack() as locks:
        for board in sorted({source_board, destination, old_board}):
            locks.enter_context(board_ownership_lock(board))
        registry = locks.enter_context(projects_db.connect_closing())
        locks.enter_context(write_txn(registry))
        old_record = projects_db.get_project(registry, expected.values[0])
        if old_record is None or old_record.board_slug != old_board:
            raise ValueError("old registered project board changed before correction")
        old = _registered_project(registry, expected.values[0], old_board, expected.project_root)
        target = _registered_project(registry, target_project_id, destination, target_root)
        metadata = kb.read_board_metadata(source_board)
        if metadata.get("archived") or metadata.get("project_id") != expected.board_project_id:
            raise ValueError("expected source board ownership changed")
        _correction_scope(old, target, source_board, destination, expected, target_directory)
        with kbc.connect_closing(board=source_board) as conn:
            task = _safe_task(conn, task_id, expected)
            projection = _binding_projection(
                conn, task, old, target, source_board, destination, reason, target_directory)
            path = Path(projection.workspace_path)
            if source_board == destination and workspace._workspace_in_use_by_other(conn, task_id, path):
                raise ValueError("destination workspace belongs to another card")
            snapshot = transfer._selected_task_rows(conn, [task_id])
        if source_board != destination:
            _verify_workspace_availability(source_board, destination, snapshot, projection)
        # Portable board export intentionally scrubs machine-local bindings. Keep
        # a separate exact local snapshot before replacing those bindings.
        recovery = Path(str(backup_path) + ".source.sqlite3")
        _private_recovery_snapshot(kb.kanban_db_path(source_board), recovery)
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(recovery)) as saved:
            saved.row_factory = sqlite3.Row
            if transfer._selected_task_rows(saved, [task_id]) != snapshot:
                raise ValueError("source changed during recovery backup; correction refused")
        if source_board != destination:
            result = transfer.merge_board(source_board, destination, backup_path, task_ids=[task_id],
                                          binding=projection, binding_source=snapshot)
            return {**result, "recovery_backup": str(recovery)}
        backup = transfer.export_board(source_board, backup_path, include_attachments=True, include_logs=True)
        with kbc.connect_closing(board=source_board) as conn, kb.write_txn(conn):
            if transfer._selected_task_rows(conn, [task_id]) != snapshot:
                raise ValueError("source card or history changed; correction refused")
            _safe_task(conn, task_id, expected)
            projection.verify_destination()
            if workspace._workspace_in_use_by_other(conn, task_id, path):
                raise ValueError("destination workspace belongs to another card")
            updated = projection.apply(task)
            conn.execute("UPDATE tasks SET " + ",".join(f"{key}=?" for key in _BINDING_FIELDS) + " WHERE id=?",
                         [*[updated[key] for key in _BINDING_FIELDS], task_id])
            projection.append_event(conn, task_id)
        return {"source": source_board, "destination": destination, "tasks": 1,
                "backup": backup["archive"], "recovery_backup": str(recovery)}
