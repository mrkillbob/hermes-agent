"""Generic public PluginContext read capability on temporary SQLite only."""

from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3

import pytest
from hermes_cli import kanban_db as kb
from hermes_cli.plugins import PluginContext, PluginManager
from hermes_cli.plugins_manifest import PluginManifest

MARKERS = ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK",
           "HERMES_KANBAN_GOAL_MODE", "HERMES_KANBAN_GOAL_MAX_TURNS",
           "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_KANBAN_DB",
           "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_KANBAN_ATTACHMENTS_ROOT")


def context(home):
    return PluginContext(PluginManifest(name="example", source="user"), PluginManager(scope_key=str(home)))


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / "owner ?#%"
    directory = home / "kanban/boards/example"
    directory.mkdir(parents=True)
    (directory / "board.json").write_text('{"slug":"example"}', encoding="utf-8")
    path = directory / "kanban.db"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(kb.SCHEMA_SQL)
        db.execute("ALTER TABLE tasks ADD COLUMN completion_contract TEXT")
        db.execute("INSERT INTO tasks(id,title,body,status,idempotency_key,created_at) VALUES(?,?,?,?,?,?)",
                   ("one", "example", "private row", "archived", "example:v2:1", 100))
        db.commit()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_CONTROL_HOME", raising=False)
    for marker in MARKERS:
        monkeypatch.delenv(marker, raising=False)
    return context(home), home, path


def test_exact_and_literal_prefix_snapshots_include_archived_without_task_changes(board, monkeypatch):
    ctx, home, path = board
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    def deny(*_a, **_k):
        pytest.fail("read capability called a mutator")
    monkeypatch.setattr(kb, "recompute_ready", deny)
    monkeypatch.setattr(kb, "init_db", deny)
    result = ctx.kanban_readonly_snapshot("example", idempotency_key="example:v2:1")
    assert result["control_root"] == str(home.resolve()) and result["board"] == "example"
    assert result["tasks"][0]["status"] == "archived"
    assert result["tasks"][0]["body"] == "private row"
    assert ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")["tasks"] == result["tasks"]
    assert ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:*")["tasks"] == []
    assert ctx.kanban_readonly_snapshot("example", idempotency_key="absent")["tasks"] == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_metadata_only_context_check_does_not_open_sqlite(board, monkeypatch):
    ctx, home, _ = board
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("metadata opened rows"))
    assert ctx.kanban_readonly_snapshot("example") == {
        "control_root": str(home.resolve()), "board": "example", "tasks": None,
    }


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("value", ["", "0", "false", "worker"])
def test_any_protected_context_presence_refuses_before_rows(board, monkeypatch, marker, value):
    ctx, _, _ = board
    monkeypatch.setenv(marker, value)
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("fenced context read rows"))
    with pytest.raises(ValueError):
        ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")
    assert marker in os.environ and os.environ[marker] == value


@pytest.mark.parametrize("field", ["HERMES_HOME", "HERMES_KANBAN_HOME", "HERMES_CONTROL_HOME"])
def test_conflicting_active_or_native_root_refuses_before_rows(board, tmp_path, monkeypatch, field):
    ctx, _, _ = board
    monkeypatch.setenv(field, str(tmp_path / "other"))
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("wrong root read rows"))
    with pytest.raises(ValueError):
        ctx.kanban_readonly_snapshot("example", idempotency_key="example:v2:1")


@pytest.mark.parametrize("selectors", [{"idempotency_key": "a", "idempotency_prefix": "a"},
                                       {"idempotency_prefix": ""}, {"idempotency_key": 1},
                                       {"idempotency_prefix": "a" * 513}])
def test_invalid_selectors_refuse_before_open(board, monkeypatch, selectors):
    ctx, _, _ = board
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("invalid selector opened SQLite"))
    with pytest.raises(ValueError):
        ctx.kanban_readonly_snapshot("example", **selectors)


def test_host_proxy_refuses_without_forwarding_untransferable_native_context():
    from types import SimpleNamespace
    from hermes_cli.plugin_host_child import RemotePluginContext
    from hermes_cli.plugin_host_wire import PluginHostUnsupported

    runtime = SimpleNamespace(ctx_call=lambda *_a, **_k: pytest.fail("RPC transferred native authority"))
    methods = [name for name in dir(PluginContext) if not name.startswith("_")]
    assert "kanban_readonly_snapshot" in methods
    proxy = RemotePluginContext(runtime, "example", {"ctx_methods": methods})
    with pytest.raises(PluginHostUnsupported):
        proxy.kanban_readonly_snapshot("example", idempotency_prefix="example:")


@pytest.mark.parametrize("fence", ["machine", "delegation"])
def test_native_in_process_fence_refuses_before_row_read(board, monkeypatch, fence):
    from agent.delegation_context import delegated_child_context

    ctx, _, _ = board
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("native fence read rows"))
    guard = kb.pin_first_board_resolution() if fence == "machine" else delegated_child_context()
    with guard, pytest.raises(ValueError):
        ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")


def test_different_owner_profile_is_refused_even_when_kanban_root_is_shared(board, monkeypatch):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override

    ctx, home, _ = board
    other = home / "profiles/other"
    other.mkdir(parents=True)
    token = set_hermes_home_override(other)
    try:
        with pytest.raises(ValueError):
            ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")
    finally:
        reset_hermes_home_override(token)


@pytest.mark.parametrize("board_name", [None, 1, True, "", "../escape", "missing"])
def test_explicit_existing_board_is_required(board, monkeypatch, board_name):
    ctx, _, _ = board
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("invalid board opened SQLite"))
    with pytest.raises(ValueError):
        ctx.kanban_readonly_snapshot(board_name, idempotency_prefix="example:")


@pytest.mark.parametrize("state", ["missing", "corrupt", "incomplete"])
def test_database_or_schema_failure_holds_without_initialization(board, state):
    ctx, _, path = board
    if state == "missing":
        path.unlink()
    elif state == "corrupt":
        path.write_bytes(b"not sqlite")
    else:
        with closing(sqlite3.connect(path)) as db:
            db.execute("ALTER TABLE tasks DROP COLUMN model_override")
            db.commit()
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(ValueError, match="^passive Kanban snapshot unavailable$"):
        ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")
    assert (path.read_bytes() if path.exists() else None) == before


def test_canonical_database_symlink_and_special_uri_path_are_pinned(board, monkeypatch):
    ctx, home, path = board
    canonical = home / "canonical ?#%.db"
    path.rename(canonical)
    path.symlink_to(canonical)
    original, opened = sqlite3.connect, []
    def connect(database, *args, **kwargs):
        opened.append(database)
        return original(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    assert len(ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")["tasks"]) == 1
    assert opened == [canonical.resolve().as_uri() + "?mode=ro"]


def test_latest_committed_wal_is_visible_and_only_sqlite_sidecars_may_appear(board):
    ctx, _, path = board
    with closing(sqlite3.connect(path)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("UPDATE tasks SET body='latest committed body'")
        writer.commit()
        before_db = path.read_bytes()
        wal = path.with_name(path.name + "-wal")
        before_wal = wal.read_bytes()
        assert ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")["tasks"][0]["body"] == "latest committed body"
        assert path.read_bytes() == before_db and wal.read_bytes() == before_wal
    assert not wal.exists() and not path.with_name(path.name + "-shm").exists()
    before_names = {p.name for p in path.parent.iterdir()}
    before_db = path.read_bytes()
    result = ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")
    assert result["tasks"][0]["body"] == "latest committed body"
    assert path.read_bytes() == before_db
    assert {p.name for p in path.parent.iterdir()} - before_names <= {path.name + "-wal", path.name + "-shm"}


@pytest.mark.parametrize("bound", ["rows", "scan", "row", "time"])
def test_complete_snapshot_bounds_return_no_partial_result(board, monkeypatch, bound):
    from hermes_cli import plugins_kanban as snapshots
    ctx, _, _ = board
    patches = {"rows": ("_MAX_ROWS", 0), "scan": ("_MAX_SCAN_BYTES", 1), "row": ("_MAX_ROW_BYTES", 16)}
    if bound == "time":
        ticks = iter([10.0, 12.0])
        monkeypatch.setattr(snapshots, "_monotonic", lambda: next(ticks, 12.0))
    else:
        name, value = patches[bound]
        monkeypatch.setattr(snapshots, name, value)
    with pytest.raises(ValueError, match="^passive Kanban snapshot unavailable$"):
        ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")


def test_snapshot_file_stays_bound_to_registered_root_after_native_environment_changes(board, tmp_path, monkeypatch):
    ctx, _, path = board
    other = tmp_path / "other/kanban/boards/example"
    other.mkdir(parents=True)
    (other / "board.json").write_text('{"slug":"example"}', encoding="utf-8")
    redirected = other / "kanban.db"
    redirected.write_bytes(path.read_bytes())
    with closing(sqlite3.connect(redirected)) as db:
        db.execute("UPDATE tasks SET body='wrong owner row'")
        db.commit()
    original = kb.board_exists
    def board_exists(slug):
        exists = original(slug)
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "other"))
        return exists
    monkeypatch.setattr(kb, "board_exists", board_exists)
    result = ctx.kanban_readonly_snapshot("example", idempotency_prefix="example:")
    assert result["tasks"][0]["body"] == "private row"
