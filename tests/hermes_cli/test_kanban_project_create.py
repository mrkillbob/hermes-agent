"""Strict creation serializes retirement, without changing ordinary project linking."""
import sqlite3

import pytest
from hermes_cli import projects_db, kanban_db
from hermes_cli.kanban_db_connect import connect_closing


@pytest.fixture
def project_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    with projects_db.connect_closing() as conn:
        project = projects_db.create_project(conn, name='Current', slug='current', board_slug='default')
    return tmp_path, project


@pytest.mark.parametrize('change', ['archive', 'delete', 'rebind', 'other_profile'])
def test_strict_creation_rejects_retired_or_foreign_identity(project_home, monkeypatch, change):
    from hermes_cli.kanban_project_create import create_task

    home, project = project_home
    with projects_db.connect_closing() as conn:
        if change == 'archive':
            projects_db.archive_project(conn, project)
        elif change == 'delete':
            projects_db.delete_project(conn, project)
        elif change == 'rebind':
            projects_db.update_project(conn, project, board_slug='other-board')
    if change == 'other_profile':
        monkeypatch.setenv('HERMES_HOME', str(home / 'other-profile'))
        with projects_db.connect_closing() as conn:
            projects_db.create_project(conn, name='Different current', slug='current', board_slug='default')
    with connect_closing(home / 'board.db') as conn:
        with pytest.raises(ValueError):
            create_task(conn, title='Discovery', project_id=project, board='default',
                        require_active_project=True, workspace_kind='dir', workspace_path=str(home))
        assert kanban_db.list_tasks(conn) == []


def test_strict_creation_holds_registry_writer_lock_through_real_insert(project_home, monkeypatch):
    from hermes_cli.kanban_project_create import create_task

    home, project = project_home
    original = kanban_db.create_task
    attempts = []

    def insert(conn, **kwargs):
        with sqlite3.connect(home / 'projects.db', timeout=0) as contender:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                contender.execute('UPDATE projects SET archived=1 WHERE id=?', (project,))
        attempts.append('retirement held')
        return original(conn, **kwargs)

    monkeypatch.setattr(kanban_db, 'create_task', insert)
    with connect_closing(home / 'board.db') as conn:
        task_id = create_task(conn, title='Discovery', project_id=project, board='default',
                              require_active_project=True, workspace_kind='dir', workspace_path=str(home))
        assert kanban_db.get_task(conn, task_id).project_id == project
    assert attempts == ['retirement held']
    with projects_db.connect_closing() as conn:
        assert projects_db.archive_project(conn, project)


def test_ordinary_creation_preserves_archived_and_missing_project_behavior(project_home):
    from hermes_cli.kanban_project_create import create_task

    home, project = project_home
    with projects_db.connect_closing() as registry:
        projects_db.archive_project(registry, project)
    with connect_closing(home / 'board.db') as conn:
        archived = create_task(conn, title='Legacy archived', project_id=project)
        missing = create_task(conn, title='Legacy missing', project_id='p_missing')
        assert kanban_db.get_task(conn, archived).project_id == project
        assert kanban_db.get_task(conn, missing).project_id is None


def test_strict_dedup_does_not_accept_task_bound_to_another_project(project_home):
    from hermes_cli.kanban_project_create import create_task

    home, project = project_home
    with connect_closing(home / 'board.db') as conn:
        existing = kanban_db.create_task(conn, title='Other', idempotency_key='collision')
        with pytest.raises(ValueError, match='binding'):
            create_task(conn, title='Discovery', project_id=project, board='default',
                        require_active_project=True, idempotency_key='collision')
        assert [task.id for task in kanban_db.list_tasks(conn)] == [existing]


def test_cli_strict_flag_enforces_current_project_and_preserves_binding(project_home):
    import json
    from hermes_cli import kanban

    _, project = project_home
    raw = kanban.run_slash(f'create Discovery --project {project} --require-active-project --workspace scratch --json')
    task = json.loads(raw)
    assert task['project_id'] == project
    with projects_db.connect_closing() as registry:
        projects_db.archive_project(registry, project)
    result = kanban.run_slash(f'create Retired --project {project} --require-active-project --workspace scratch --json')
    assert 'current canonical project' in result
    with connect_closing() as conn:
        assert [row.title for row in kanban_db.list_tasks(conn)] == ['Discovery']


def test_strict_creation_refuses_outer_board_transaction_without_inserting(project_home):
    from hermes_cli.kanban_project_create import create_task

    home, project = project_home
    with connect_closing(home / 'board.db') as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='active.*transaction'):
            create_task(conn, title='Uncommitted discovery', project_id=project,
                        board='default', require_active_project=True)
        assert conn.in_transaction
        assert kanban_db.list_tasks(conn) == []
        conn.rollback()
    with connect_closing(home / 'board.db') as conn:
        assert kanban_db.list_tasks(conn) == []


def test_ordinary_creation_preserves_outer_board_transaction(project_home):
    from hermes_cli.kanban_project_create import create_task

    home, project = project_home
    with connect_closing(home / 'board.db') as conn:
        conn.execute('BEGIN IMMEDIATE')
        task_id = create_task(conn, title='Caller owned transaction', project_id=project)
        assert conn.in_transaction
        assert kanban_db.get_task(conn, task_id).project_id == project
        conn.rollback()
        assert kanban_db.list_tasks(conn) == []


@pytest.mark.parametrize('board', ['DEFAULT', ' default '])
def test_cli_strict_creation_normalizes_explicit_board(project_home, board):
    import json
    from hermes_cli import kanban

    _, project = project_home
    result = kanban.run_slash(
        f'--board "{board}" create Discovery --project {project} '
        '--require-active-project --workspace scratch --json')
    assert json.loads(result)['project_id'] == project
