"""Archived board databases remain in cross-board workspace ownership scans."""

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_workspace as workspace


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _archived_board_with_live_workspace(slug: str, path: Path):
    kb.create_board(slug)
    with kbc.connect_closing(board=slug) as conn:
        task_id = kb.create_task(conn, title="archived board still owns workspace")
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET workspace_kind='scratch', workspace_path=? WHERE id=?",
                (str(path), task_id),
            )
    result = kb.remove_board(slug, archive=True)
    return task_id, Path(result["new_path"]) / "kanban.db"


def test_active_sibling_still_owns_a_shared_workspace(hermes_home):
    shared = hermes_home / "kanban" / "workspaces" / "active-sharer"
    shared.mkdir(parents=True)
    kb.create_board("active-owner")
    with kbc.connect_closing(board="active-owner") as conn:
        task_id = kb.create_task(conn, title="active board still owns workspace")
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET workspace_kind='scratch', workspace_path=? WHERE id=?",
                (str(shared), task_id),
            )
    with kbc.connect_closing() as conn:
        siblings = workspace._sibling_board_db_files(conn)
        active_db = kb.boards_root() / "active-owner" / "kanban.db"
        assert active_db.resolve() in siblings
        assert workspace._other_board_uses_path(
            active_db, task_id, workspace._path_key(shared.resolve())
        )


def test_archived_sibling_is_scanned_but_empty_container_db_is_not(hermes_home):
    shared = hermes_home / "kanban" / "workspaces" / "shared"
    shared.mkdir(parents=True)
    archived_task, archived_db = _archived_board_with_live_workspace("former-owner", shared)

    archive_root = kb.boards_root() / "_archived"
    container_db = archive_root / "kanban.db"
    container_db.touch()
    with kbc.connect_closing() as conn:
        siblings = workspace._sibling_board_db_files(conn)
        assert archived_db.resolve() in siblings
        assert container_db.resolve() not in siblings
        assert workspace._other_board_uses_path(
            archived_db, archived_task, workspace._path_key(shared.resolve())
        )
    assert container_db.stat().st_size == 0


def test_unmarked_archived_database_makes_cleanup_unknown_and_preserves_workspace(hermes_home):
    shared = hermes_home / "kanban" / "workspaces" / "in-use"
    shared.mkdir(parents=True)
    (shared / "keep.txt").write_text("still owned", encoding="utf-8")
    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="archived scratch")
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET status='archived', workspace_kind='scratch', "
                "workspace_path=? WHERE id=?",
                (str(shared), task_id),
            )

    malformed = kb.boards_root() / "_archived" / "legacy-1700000000"
    malformed.mkdir(parents=True)
    (malformed / "kanban.db").touch()
    with kbc.connect_closing() as conn:
        assert workspace._workspace_in_use_by_other(conn, task_id, shared) == "unknown"
        workspace._cleanup_workspace(conn, task_id)
        assert "unknown" in _deferred_reasons(conn, task_id)
    assert (shared / "keep.txt").read_text(encoding="utf-8") == "still owned"


def test_invalid_archived_sibling_database_fails_closed(hermes_home):
    shared = hermes_home / "kanban" / "workspaces" / "preserve"
    shared.mkdir(parents=True)
    (shared / "keep.txt").write_text("preserve", encoding="utf-8")
    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="archived scratch")
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET status='archived', workspace_kind='scratch', "
                "workspace_path=? WHERE id=?",
                (str(shared), task_id),
            )

    archived_dir = kb.boards_root() / "_archived" / "former-1700000000"
    archived_dir.mkdir(parents=True)
    (archived_dir / "board.json").write_text(
        json.dumps({"slug": "former"}), encoding="utf-8"
    )
    (archived_dir / "kanban.db").write_bytes(b"not a sqlite database")
    with kbc.connect_closing() as conn:
        assert workspace._workspace_in_use_by_other(conn, task_id, shared) == "unknown"
        workspace._cleanup_workspace(conn, task_id)
    assert (shared / "keep.txt").read_text(encoding="utf-8") == "preserve"


def test_oversized_archive_marker_makes_cleanup_unknown(hermes_home):
    shared = hermes_home / "kanban" / "workspaces" / "oversized-marker"
    shared.mkdir(parents=True)
    (shared / "keep.txt").write_text("preserve", encoding="utf-8")
    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="archived scratch")
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET status='archived', workspace_kind='scratch', "
                "workspace_path=? WHERE id=?",
                (str(shared), task_id),
            )

    archived_dir = kb.boards_root() / "_archived" / "oversized-1700000000"
    archived_dir.mkdir(parents=True)
    (archived_dir / "board.json").write_bytes(
        b'{"slug":"oversized"}' + b" " * workspace._MAX_ARCHIVED_BOARD_MARKER_BYTES
    )
    (archived_dir / "kanban.db").touch()
    with kbc.connect_closing() as conn:
        assert workspace._workspace_in_use_by_other(conn, task_id, shared) == "unknown"
        workspace._cleanup_workspace(conn, task_id)
        assert "unknown" in _deferred_reasons(conn, task_id)
    assert (shared / "keep.txt").read_text(encoding="utf-8") == "preserve"


def test_nonempty_archive_container_database_refuses_cleanup(hermes_home):
    shared = hermes_home / "kanban" / "workspaces" / "container-database"
    shared.mkdir(parents=True)
    (shared / "keep.txt").write_text("preserve", encoding="utf-8")
    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="archived scratch")
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET status='archived', workspace_kind='scratch', "
                "workspace_path=? WHERE id=?",
                (str(shared), task_id),
            )

    container_db = kb.boards_root() / "_archived" / "kanban.db"
    container_db.parent.mkdir(parents=True)
    container_db.write_bytes(b"unrecognized archive data")
    with kbc.connect_closing() as conn:
        assert workspace._workspace_in_use_by_other(conn, task_id, shared) == "unknown"
        workspace._cleanup_workspace(conn, task_id)
        assert "unknown" in _deferred_reasons(conn, task_id)
    assert (shared / "keep.txt").read_text(encoding="utf-8") == "preserve"
    assert container_db.read_bytes() == b"unrecognized archive data"


def test_archive_marker_replaced_while_reading_is_rejected(tmp_path, monkeypatch):
    marker = tmp_path / "board.json"
    original = b'{"slug":"board"}'
    marker.write_bytes(original)
    real_read = workspace.os.read

    def replace_after_read(fd, size):
        contents = real_read(fd, size)
        marker.write_bytes(original + b" ")
        return contents

    monkeypatch.setattr(workspace.os, "read", replace_after_read)
    with pytest.raises(OSError, match="changed while reading"):
        workspace._read_archived_board_marker(marker)


def _deferred_reasons(conn, task_id):
    return [
        json.loads(row["payload"])["reason"]
        for row in conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? "
            "AND kind='workspace_cleanup_deferred_shared' ORDER BY id",
            (task_id,),
        )
    ]
