"""Existing-board constructor regressions using disposable homes and SQLite."""
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from hermes_constants import (get_default_hermes_root, get_hermes_home, reset_hermes_home_override,
                              set_hermes_home_override)
from hermes_cli import kanban_artifact_disposition as artifact
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import sqlite_safe_read, startup_hold
from agent.delegation_context import delegated_child_context
import hermes_state_wal as wal


@pytest.fixture
def real_home(tmp_path, monkeypatch):
    # The outer driver must have rejected inherited board/authority pins first.
    home = tmp_path / 'native-home'
    home.mkdir()
    root = home / '.hermes'
    root.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('LOCALAPPDATA', str(home / 'AppData' / 'Local'))
    monkeypatch.setenv('APPDATA', str(home / 'AppData' / 'Roaming'))
    monkeypatch.setenv('HERMES_HOME', str(root))
    token = set_hermes_home_override(root)
    try:
        assert Path.home() == home
        assert get_hermes_home() == root
        assert get_default_hermes_root() == root
        path = kb.kanban_db_path(board='default')
        assert path == root / 'kanban.db' and path.resolve().is_relative_to(root.resolve())
        assert startup_hold.marker_path().parent == root
        assert startup_hold.journal_path().parent == root
        assert not startup_hold.active()
        yield path
    finally:
        reset_hermes_home_override(token)


def make_board(path, mode):
    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert conn.execute('PRAGMA journal_mode=' + mode).fetchone()[0].lower() == mode.lower()
        conn.executescript('''CREATE TABLE tasks(id TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE notes(id INTEGER PRIMARY KEY, data BLOB);
            CREATE INDEX notes_data ON notes(data);
            CREATE TRIGGER note_guard BEFORE DELETE ON notes BEGIN SELECT RAISE(ABORT, 'guard'); END;
            INSERT INTO tasks VALUES ('t_fixture', 'unchanged');
            INSERT INTO notes VALUES (1, x'00ff');''')


def inventory(conn):
    schema = tuple(tuple(r) for r in conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name, tbl_name"))
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' "
                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    rows = {}
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        # Sorting repr handles mixed SQLite scalar types without relying on rowid.
        rows[name] = sorted((tuple(r) for r in conn.execute('SELECT * FROM ' + quoted)), key=repr)
    return schema, rows, conn.execute('PRAGMA journal_mode').fetchone()[0].lower()


def before_or_after(path):
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return inventory(conn)


def unchanged(path, pre):
    assert not sqlite_safe_read.has_live_connection(path)
    assert before_or_after(path) == pre


@pytest.mark.parametrize('mode', ['WAL', 'DELETE'])
@pytest.mark.parametrize('vulnerable', [False, True])
def test_real_constructor_preserves_inventory_and_durability(real_home, monkeypatch, caplog, mode, vulnerable):
    make_board(real_home, mode)
    pre = before_or_after(real_home)
    monkeypatch.setattr(wal, 'is_sqlite_wal_reset_vulnerable', lambda: vulnerable)
    def forbidden(*args, **kwargs): pytest.fail('journal switch or ordinary policy invoked')
    monkeypatch.setattr(wal, '_set_journal_mode_no_wait', forbidden)
    monkeypatch.setattr(wal, 'apply_wal_with_fallback', forbidden)
    with caplog.at_level('WARNING'):
        with artifact.existing_artifact_connection(board='default', preserve_current_journal=True) as conn:
            assert sqlite_safe_read.has_live_connection(real_home)
            assert inventory(conn) == pre
            assert conn.total_changes == 0
            assert conn.row_factory is sqlite3.Row and conn.text_factory is kb._lossy_text
            for key, expected in [('synchronous', 2), ('foreign_keys', 1), ('secure_delete', 1),
                                  ('cell_size_check', 1), ('wal_autocheckpoint', 100),
                                  ('journal_size_limit', 8388608)]:
                assert conn.execute('PRAGMA ' + key).fetchone()[0] == expected
            assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == kbc._resolve_busy_timeout_ms()
            if mode == 'WAL' and sys.platform == 'darwin':
                assert conn.execute('PRAGMA checkpoint_fullfsync').fetchone()[0] == 1
    if mode == 'WAL' and vulnerable:
        assert 'vulnerable' in caplog.text and 'WAL' in caplog.text
    unchanged(real_home, pre)


def test_default_routes_to_normal_policy_without_running_it(real_home, monkeypatch):
    make_board(real_home, 'DELETE')
    pre = before_or_after(real_home)
    class Dispatched(Exception): pass
    def spy(*args, **kwargs): raise Dispatched()
    monkeypatch.setattr(wal, 'apply_wal_with_fallback', spy)
    with pytest.raises(Dispatched):
        with artifact.existing_artifact_connection(board='default'): pass
    unchanged(real_home, pre)


@pytest.mark.parametrize('value', [None, 0, 1, 'true', []])
def test_exact_boolean_refusal_before_creation(real_home, value):
    with pytest.raises(ValueError):
        with artifact.existing_artifact_connection(board='default', preserve_current_journal=value): pass
    assert not real_home.exists()


def test_real_missing_empty_slug_schema_and_delegation(real_home):
    with pytest.raises(ValueError):
        with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
    assert not real_home.exists()
    real_home.touch()
    with pytest.raises(ValueError):
        with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
    assert not sqlite_safe_read.has_live_connection(real_home)
    real_home.unlink()
    with contextlib.closing(sqlite3.connect(real_home)) as conn:
        conn.execute('CREATE TABLE unrelated(id INTEGER PRIMARY KEY)')
    pre_missing_schema = before_or_after(real_home)
    with pytest.raises(ValueError):
        with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
    unchanged(real_home, pre_missing_schema)
    real_home.unlink()
    make_board(real_home, 'DELETE')
    pre = before_or_after(real_home)
    with pytest.raises(ValueError):
        with artifact.existing_artifact_connection(board='../escape', preserve_current_journal=True): pass
    with delegated_child_context():
        with pytest.raises(PermissionError):
            with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
    unchanged(real_home, pre)


@pytest.mark.parametrize('case', ['unknown', 'changed'])
def test_probe_refusal_closes_tracking(real_home, monkeypatch, case):
    make_board(real_home, 'DELETE')
    pre = before_or_after(real_home)
    values = iter([None] if case == 'unknown' else ['delete', 'wal'])
    monkeypatch.setattr(wal, '_on_disk_journal_mode', lambda conn: next(values))
    def no_switch(*args, **kwargs): pytest.fail('journal switch attempted')
    monkeypatch.setattr(wal, '_set_journal_mode_no_wait', no_switch)
    with pytest.raises(sqlite3.OperationalError):
        with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
    unchanged(real_home, pre)


def test_cross_vm_warning_for_real_wal(real_home, monkeypatch, caplog):
    make_board(real_home, 'WAL')
    pre = before_or_after(real_home)
    monkeypatch.setattr(wal, '_path_on_cross_vm_fs', lambda path: True)
    with caplog.at_level('WARNING'):
        with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
    assert 'cross' in caplog.text.lower() or 'virtiofs' in caplog.text.lower()
    unchanged(real_home, pre)


def test_failure_after_open_closes_tracking(real_home, monkeypatch):
    make_board(real_home, 'DELETE')
    pre = before_or_after(real_home)
    def interrupted(*args, **kwargs): raise KeyboardInterrupt()
    monkeypatch.setattr(wal, 'preserve_existing_journal_mode', interrupted)
    with pytest.raises(KeyboardInterrupt):
        with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
    unchanged(real_home, pre)


def test_existing_schema_only_private_contract(real_home):
    make_board(real_home, 'DELETE')
    pre = before_or_after(real_home)
    for existing, schema in [(False, False), (True, False), (False, True)]:
        with pytest.raises(ValueError):
            kbc._open_configured(real_home, kbc._schema_is_present, existing_only=existing,
                                 require_schema=schema, preserve_current_journal=True)
    unchanged(real_home, pre)


@pytest.mark.parametrize('marker_body,held', [
    ({'action': 'drain'}, False),
    ({'action': 'drain', 'startup_hold': {}}, True),
    ({}, True),
])
def test_real_hold_reader_on_synthetic_marker(real_home, marker_body, held):
    make_board(real_home, 'DELETE')
    pre = before_or_after(real_home)
    marker = startup_hold.marker_path()
    marker.write_text(json.dumps(marker_body), encoding='utf-8')
    try:
        assert startup_hold.active() is held
        if held:
            with pytest.raises(sqlite3.OperationalError):
                with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
        else:
            with artifact.existing_artifact_connection(board='default', preserve_current_journal=True) as conn:
                assert inventory(conn) == pre
        unchanged(real_home, pre)
    finally:
        marker.unlink()


def test_real_hold_reader_on_synthetic_journal(real_home):
    make_board(real_home, 'DELETE')
    pre = before_or_after(real_home)
    journal = startup_hold.journal_path()
    journal.write_text('{}', encoding='utf-8')
    try:
        assert startup_hold.active()
        with pytest.raises(sqlite3.OperationalError):
            with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
        unchanged(real_home, pre)
    finally:
        journal.unlink()


def test_real_inherited_fence_marker(real_home, monkeypatch):
    make_board(real_home, 'DELETE')
    pre = before_or_after(real_home)
    # Driver must refuse a real inherited marker; this is a test-owned synthetic one.
    assert not os.environ.get('HERMES_DELEGATED_CHILD_CONTEXT')
    with monkeypatch.context() as scoped:
        scoped.setenv('HERMES_DELEGATED_CHILD_CONTEXT', str(real_home.parent))
        with pytest.raises(PermissionError):
            with artifact.existing_artifact_connection(board='default', preserve_current_journal=True): pass
    unchanged(real_home, pre)
