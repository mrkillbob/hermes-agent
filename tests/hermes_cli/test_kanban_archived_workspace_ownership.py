"""Archived board databases remain in cross-board workspace ownership scans."""

import json
from io import StringIO
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


def _archived_board_with_live_workspace(slug: str, path: Path, **metadata):
    kb.create_board(slug, **metadata)
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


def test_writer_produced_large_display_metadata_does_not_hide_archived_owner(hermes_home):
    shared = hermes_home / "kanban" / "workspaces" / "large-display-metadata"
    shared.mkdir(parents=True)
    task_id, archived_db = _archived_board_with_live_workspace(
        "large-description", shared, description="d" * 20_000, icon="i" * 2_000,
    )

    with kbc.connect_closing() as conn:
        archived = archived_db.resolve()
        assert archived in workspace._sibling_board_db_files(conn)
        assert workspace._other_board_uses_path(
            archived, task_id, workspace._path_key(shared.resolve())
        )


def test_streamed_marker_parser_bounds_reads_and_validates_the_whole_document():
    from hermes_cli.kanban_archive_marker import read_board_slug

    def assert_matches_previous_reader(document, *, chunk_size=4096):
        previous_slug = kb._require_slug(json.loads(document).get("slug"))
        streamed_slug = kb._require_slug(
            read_board_slug(StringIO(document), chunk_size=chunk_size)
        )
        assert streamed_slug == previous_slug

    class BoundedReader(StringIO):
        def __init__(self, value):
            super().__init__(value)
            self.requests = []

        def read(self, size=-1):
            self.requests.append(size)
            assert 0 < size <= 4096
            return super().read(size)

    reader = BoundedReader(
        '{"description":"' + ("x" * 20_000) + '","sl\\u0075g":"large-board"}'
    )
    assert read_board_slug(reader, chunk_size=4096) == "large-board"
    assert reader.requests and max(reader.requests) == 4096

    malformed = BoundedReader(
        '{"slug":"board","display":"' + ("x" * 20_000) + '"} trailing'
    )
    with pytest.raises(ValueError, match="trailing data"):
        read_board_slug(malformed, chunk_size=4096)
    assert read_board_slug(
        StringIO(
            '{"sl\\u0075g":"b\\u006fard","nested":{"slug":"wrong",'
            '"rows":[1,{"slug":"also-wrong"}]}}'
        ),
        chunk_size=7,
    ) == "board"
    # Preserve json.loads last-key-wins behavior, even when an earlier slug
    # value would not itself pass the board slug validator.
    assert_matches_previous_reader('{"slug":[],"slug":"later-board"}', chunk_size=3)
    assert_matches_previous_reader('{"slug":"first-board","slug":"last-board"}')

    # The previous reader normalized arbitrary JSON scalar values through
    # _require_slug, including booleans and numbers, and stripped Unicode
    # whitespace before applying the 64-character slug limit.
    for value, expected in ((123, "123"), (True, "true"), (False, "false")):
        document = json.dumps({"slug": value})
        assert_matches_previous_reader(document)
        assert kb._require_slug(read_board_slug(StringIO(document))) == expected
    padded = '{"slug":"' + ("\\u00a0" * 300) + "MiXeD" + ("\\u2003" * 300) + '"}'
    assert_matches_previous_reader(padded, chunk_size=5)
    assert kb._require_slug(read_board_slug(StringIO(padded), chunk_size=5)) == "mixed"

    # Python's prior json.loads reader accepts these constants in ignored
    # metadata; keep that compatibility while still validating the document.
    constants = '{"slug":"owner","unused":[NaN,Infinity,-Infinity]}'
    assert_matches_previous_reader(constants)
    assert kb._require_slug(read_board_slug(StringIO(constants))) == "owner"
    for invalid in (
        '{"slug":"board","value":"\\q"}',
        '{"slug":"board","value":01}',
        '{"slug":"board",}',
        '{"slug":"board","value":[1,]}',
        '{"slug":"board","value":NaNx}',
    ):
        with pytest.raises(ValueError):
            read_board_slug(StringIO(invalid), chunk_size=7)


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


def test_malformed_large_archive_marker_makes_cleanup_unknown(hermes_home):
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
        b'{"slug":"oversized"}' + b" " * 20_000 + b" trailing"
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
    from hermes_cli import kanban_archive_marker

    real_parse = kanban_archive_marker.read_board_slug

    def replace_after_read(stream, *, chunk_size):
        slug = real_parse(stream, chunk_size=chunk_size)
        marker.write_bytes(original + b" ")
        return slug

    monkeypatch.setattr(kanban_archive_marker, "read_board_slug", replace_after_read)
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
