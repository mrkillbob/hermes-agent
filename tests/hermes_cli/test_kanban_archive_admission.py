"""Archiving must not publish history that the ownership census cannot admit."""
import hashlib
import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_workspace as workspace


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(name, raising=False)
    kb.create_board('candidate', description='d' * 20_000)
    kb.set_current_board('candidate')
    return kb.board_dir('candidate')


@pytest.mark.parametrize('damage', ['missing_marker', 'invalid_marker', 'wrong_slug',
                                    'symlink_marker', 'missing_database', 'symlink_database'])
def test_archive_refuses_incomplete_identity_before_state_or_directory_changes(board, damage):
    marker, database = board / 'board.json', board / 'kanban.db'
    if damage == 'missing_marker':
        marker.unlink()
    if damage == 'invalid_marker':
        marker.write_text('{broken')
    if damage == 'wrong_slug':
        marker.write_text(json.dumps({'slug': 'another-board'}))
    if damage == 'symlink_marker':
        target = board.parent / 'saved-marker.json'
        marker.rename(target)
        marker.symlink_to(target)
    if damage == 'missing_database':
        database.unlink()
    if damage == 'symlink_database':
        target = board.parent / 'saved.sqlite3'
        database.rename(target)
        database.symlink_to(target)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
              for path in (marker, database)}
    initialized = set(kb._INITIALIZED_PATHS)
    selector = kb.current_board_path().read_bytes()
    with pytest.raises(ValueError, match='identity'):
        kb.remove_board('candidate', archive=True)
    assert board.is_dir() and not (board.parent / '_archived').exists()
    assert kb.current_board_path().read_bytes() == selector
    assert kb._INITIALIZED_PATHS == initialized
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            for path in (marker, database)} == before


def test_valid_large_marker_archive_remains_in_workspace_ownership_census(board):
    shared = board.parent / 'owned-workspace'
    with kbc.connect_closing(board='candidate') as conn:
        tid = kb.create_task(conn, title='Retained owner', board='candidate')
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET workspace_kind='scratch',workspace_path=? WHERE id=?", (str(shared), tid))
    result = kb.remove_board('candidate', archive=True)
    archived = Path(result['new_path']) / 'kanban.db'
    assert archived in workspace._archived_board_db_files(archived.parent.parent)
    assert workspace._other_board_uses_path(archived, tid, workspace._path_key(shared.resolve()))
