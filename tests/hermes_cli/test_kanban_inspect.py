"""Inspection uses existing schema and returns full card revision inputs only."""
import json
import sqlite3
from contextlib import closing

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_inspect import inspect_tasks, main
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows
from hermes_cli.sqlite_readonly import readonly_snapshot


def test_inspects_two_cards_without_initializing_or_disposing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_BOARD', raising=False)
    with kbc.connect_closing(board='default') as conn:
        ids = [kb.create_task(conn, title=f'card {i}', body='Original acceptance brief', board='default')
               for i in range(2)]
        kb.add_comment(conn, ids[0], 'reviewer', 'Acceptance evidence')
    path = kb.kanban_db_path(board='default')
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail('inspection must not initialize, migrate or dispose')

    monkeypatch.setattr(kbc, '_open_configured', forbidden)
    monkeypatch.setattr(kb, 'complete_task', forbidden)
    with readonly_snapshot(path) as reader:
        with pytest.raises(ValueError, match='size limit'):
            _selected_task_rows(reader, ids, max_bytes=1)
    result = inspect_tasks(path, 'default', ids)
    assert result['admission_granted'] is False
    assert len(result['cards']) == 2
    for card in result['cards']:
        assert card['rows']['tasks'][0]['body'] == 'Original acceptance brief'
        assert card['revision'] == task_revision('default', card['task_id'], card['rows'])
    assert result['cards'][0]['rows']['task_comments'][0]['body'] == 'Acceptance evidence'
    assert main(['--database', str(path), '--board', 'default', *ids]) == 0
    assert json.loads(capsys.readouterr().out)['cards'] == result['cards']
    assert path.read_bytes() == before


def test_legacy_schema_reports_error_without_migration_or_partial_output(tmp_path, capsys):
    path = tmp_path / 'legacy.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE tasks(id TEXT)')
        conn.commit()
    before = path.read_bytes()
    assert main(['--database', str(path), '--board', 'default', 't_12345678']) == 1
    output = capsys.readouterr()
    assert not output.out
    error = json.loads(output.err)
    assert error['sqlite_errorname'] == 'SQLITE_ERROR'
    assert path.read_bytes() == before


@pytest.mark.parametrize('ids', [[], ['t_12345678'] * 2, ['bad-id']])
def test_invalid_selection_never_creates_database(tmp_path, ids):
    path = tmp_path / 'absent.db'
    with pytest.raises(ValueError):
        inspect_tasks(path, 'default', ids)
    assert not path.exists()


def test_missing_card_returns_no_partial_result(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_BOARD', raising=False)
    with kbc.connect_closing(board='default') as conn:
        tid = kb.create_task(conn, title='existing', board='default')
    path = kb.kanban_db_path(board='default')
    assert main(['--database', str(path), '--board', 'default', tid, 't_00000000']) == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)['error'] == 'ValueError'
