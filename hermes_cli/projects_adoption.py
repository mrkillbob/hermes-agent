"""Trusted operator adoption of an existing canonical project into one named profile.

This is data repair, not an authorization capability. The root-operator CLI
consumer and its task/delegation guard live in ``projects_cmd``. Adoption grants
no execution or credential access and creates no model/RPC capability.
"""

from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3

from hermes_cli import kanban_db as kb, projects_db
from hermes_cli.kanban_db_boards import board_ownership_lock
from hermes_cli.kanban_db_workspace import _git_toplevel, _git_common_dir, _path_key
from hermes_cli.profiles import (
    _get_default_hermes_home,
    get_profile_dir,
    validate_profile_name,
)
from hermes_cli.profiles_lifecycle import profile_lifecycle_reservation
from hermes_cli.sqlite_util import write_txn
from hermes_constants import named_profile_is_live

_PROJECT_COLUMNS = {
    "id",
    "slug",
    "name",
    "description",
    "icon",
    "color",
    "board_slug",
    "primary_path",
    "created_at",
    "archived",
}
_FOLDER_COLUMNS = {"project_id", "path", "label", "is_primary", "added_at"}


def _existing_path(value):
    path = Path(value)
    if (
        not path.is_absolute()
        or path != path.resolve()
        or any(p.is_symlink() for p in (path, *path.parents))
    ):
        raise ValueError("adoption requires absolute canonical paths without symlinks")
    if not path.is_file() or path.name != "projects.db":
        raise ValueError("adoption requires an existing projects.db registry")
    return path


def _identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino


def _paths(source_registry, target_registry, target_profile):
    source, target = map(_existing_path, (source_registry, target_registry))
    validate_profile_name(target_profile)
    if (
        target_profile == "default"
        or source.parent.resolve() != _get_default_hermes_home().resolve()
    ):
        raise ValueError(
            "adoption requires the configured profile root registry and a named profile"
        )
    if source.parent.parent.name == "profiles":
        raise ValueError("source must be the root registry")
    if target != source.parent / "profiles" / target_profile / "projects.db":
        raise ValueError("target registry does not match the explicit named profile")
    if target.parent != get_profile_dir(target_profile).resolve():
        raise ValueError(
            "target registry does not match the configured profile lifecycle root"
        )
    if not named_profile_is_live(target.parent) or _identity(source) == _identity(
        target
    ):
        raise ValueError("target must be a distinct live profile registry")
    return source, target


def _open_existing(path):
    # Avoid projects_db.connect: it may initialize schema or alter journal mode.
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _schema(conn):
    for table, expected in (
        ("projects", _PROJECT_COLUMNS),
        ("project_folders", _FOLDER_COLUMNS),
    ):
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if columns != expected:
            raise ValueError("adoption refuses unsupported registry schema")


def _root_identity(root):
    path = Path(root)
    if not path.is_absolute() or path != path.resolve() or _git_toplevel(path) != path:
        raise ValueError("expected root must be a canonical Git repository top level")
    common = _git_common_dir(path)
    if common is None:
        raise ValueError("expected Git root has no common repository directory")
    return _identity(path), common, _identity(common)


def _source_binding(conn, identity, root, board):
    row = conn.execute("SELECT * FROM projects WHERE id=?", (identity,)).fetchone()
    if (
        row is None
        or row["archived"] != 0
        or row["primary_path"] != str(root)
        or row["board_slug"] != board
        or projects_db.normalize_slug(row["slug"]) != row["slug"]
    ):
        raise ValueError("canonical source project identity/root/board changed")
    metadata = kb.read_board_metadata(board)
    if (
        not kb.board_exists(board)
        or metadata.get("archived")
        or metadata.get("project_id") != identity
    ):
        raise ValueError("canonical project and shared board ownership do not agree")
    folders = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM project_folders WHERE project_id=? AND is_primary!=0",
            (identity,),
        )
    ]
    if (
        len(folders) != 1
        or folders[0]["path"] != str(root)
        or folders[0]["is_primary"] != 1
    ):
        raise ValueError("canonical source primary folder is inconsistent")
    project = {
        key: row[key]
        for key in (
            "id",
            "slug",
            "name",
            "board_slug",
            "primary_path",
            "created_at",
            "archived",
        )
    }
    project.update(description=None, icon=None, color=None)
    return project, folders[0]


def _target_state(conn, project, folder):
    existing = None
    root_key = _path_key(Path(project["primary_path"]).resolve())
    for row in conn.execute("SELECT * FROM projects"):
        if row["id"] == project["id"]:
            existing = dict(row)
        elif row["slug"] == project["slug"] or (
            row["primary_path"]
            and _path_key(Path(row["primary_path"]).resolve()) == root_key
        ):
            raise ValueError(
                "target project ID/slug/root conflicts with canonical binding"
            )
    for row in conn.execute("SELECT * FROM project_folders"):
        if (
            row["project_id"] != project["id"]
            and _path_key(Path(row["path"]).resolve()) == root_key
        ):
            raise ValueError("target folder already claims canonical root")
    own = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM project_folders WHERE project_id=?", (project["id"],)
        )
    ]
    if existing is None and not own:
        if projects_db.get_active_id(conn) == project["id"]:
            raise ValueError(
                "stale active project pointer would activate adopted binding"
            )
        return "adopted"
    if existing == project and own == [folder]:
        return "already_adopted"
    raise ValueError("existing target binding is not the exact canonical projection")


def _insert_binding(conn, project, folder):
    for table, record in (("projects", project), ("project_folders", folder)):
        columns = list(record)
        conn.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [record[key] for key in columns],
        )


def _unrelated_state(conn, identity):
    result = {}
    for table, key in (("projects", "id"), ("project_folders", "project_id")):
        result[table] = [
            tuple(row)
            for row in conn.execute(
                f"SELECT * FROM {table} WHERE {key}!=? ORDER BY rowid", (identity,)
            )
        ]
    for table in ("project_meta", "discovered_repos"):
        result[table] = [
            tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
        ]
    return result


def adopt_canonical_project(
    *,
    source_registry,
    target_registry,
    target_profile,
    expected_project_id,
    expected_root,
    expected_board,
):
    """Adopt only one reviewed binding; preserve source, board and target selection.

    Explicit operator paths do not bypass OS permissions or authorize callers.
    Only the target registry changes. Existing cooperative ownership writers are
    serialized; external same-user filesystem replacement is not containment.
    """
    source, target = _paths(source_registry, target_registry, target_profile)
    with profile_lifecycle_reservation(target.parent):
        result = _adopt_reserved(
            source_registry=source,
            target_registry=target,
            target_profile=target_profile,
            expected_project_id=expected_project_id,
            expected_root=expected_root,
            expected_board=expected_board,
        )
        # Detect non-cooperating filesystem/marker mutation at commit as an uncertain
        # outcome, never a successful adoption of an unlinked or retired profile.
        try:
            _paths(source, target, target_profile)
        except ValueError as exc:
            raise ValueError(
                "adoption committed but profile identity changed; inspect original outcome before retry"
            ) from exc
        return result


def _adopt_reserved(
    *,
    source_registry,
    target_registry,
    target_profile,
    expected_project_id,
    expected_root,
    expected_board,
):
    source, target = _paths(source_registry, target_registry, target_profile)
    if kb._normalize_board_slug(expected_board) != expected_board or not expected_board:
        raise ValueError("adoption requires an exact canonical board slug")
    root = Path(expected_root)
    root_identity = _root_identity(root)
    identities = {path: _identity(path) for path in (source, target)}
    with ExitStack() as stack:
        # Metadata producers already take the board ownership lock before
        # project-registry writers. Match that order, then let project CRUD's
        # registry -> board-DB order proceed without an inversion.
        stack.enter_context(board_ownership_lock(expected_board))
        conns = {}
        for path in sorted((source, target), key=lambda value: _path_key(value)):
            conn = stack.enter_context(closing(_open_existing(path)))
            stack.enter_context(write_txn(conn))
            _schema(conn)
            conns[path] = conn
        if _paths(source, target, target_profile) != (source, target):
            raise ValueError("registry path changed")
        _confirm_identities(identities, root, root_identity)
        project, folder = _source_binding(
            conns[source], expected_project_id, root, expected_board
        )
        status = _target_state(conns[target], project, folder)
        unrelated = _unrelated_state(conns[target], project["id"])
        if status == "adopted":
            _insert_binding(conns[target], project, folder)
        if _target_state(conns[target], project, folder) != "already_adopted":
            raise ValueError("target canonical binding was not confirmed")
        if _unrelated_state(conns[target], project["id"]) != unrelated:
            raise ValueError("adoption changed unrelated target state")
        _paths(source, target, target_profile)
        _confirm_identities(identities, root, root_identity)
        if _source_binding(
            conns[source], expected_project_id, root, expected_board
        ) != (project, folder):
            raise ValueError("source binding changed before target commit")
        return {
            "status": status,
            "project_id": project["id"],
            "board": expected_board,
            "root": str(root),
            "target_profile": target_profile,
        }


def _confirm_identities(registries, root, expected):
    if any(_identity(path) != identity for path, identity in registries.items()):
        raise ValueError("registry file identity changed")
    if _root_identity(root) != expected:
        raise ValueError("canonical Git root identity changed")
