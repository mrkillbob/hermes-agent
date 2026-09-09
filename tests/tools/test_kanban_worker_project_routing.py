"""Workers retain board-owned project identity across profile-local registries."""
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize('reference', ['id', 'slug', 'inherited'])
def test_worker_child_uses_parent_project_from_shared_board_home(tmp_path, monkeypatch, reference):
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, projects_db as pdb
    from hermes_cli import profiles
    from tools import kanban_tools  # noqa: F401
    from tools.registry import registry

    root = tmp_path / '.hermes'
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(root))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.setattr(profiles, 'profile_exists', lambda _: True)
    repo = tmp_path / 'hermes-source'
    repo.mkdir()
    with pdb.connect_closing() as conn:
        project = pdb.create_project(conn, name='Hermes', slug='hermes-agent', primary_path=str(repo))
    with kbc.connect() as conn:
        parent = kb.create_task(conn, title='Discovery', assignee='architect', project_id=project,
                                workspace_kind='dir', workspace_path=str(tmp_path / 'discovery'))
    monkeypatch.setenv('HERMES_HOME', str(root / 'profiles/architect'))
    monkeypatch.setenv('HERMES_PROFILE', 'architect')
    monkeypatch.setenv('HERMES_KANBAN_TASK', parent)
    args = dict(title='Repair', assignee='coding-expert', parents=[parent])
    if reference != 'inherited':
        args['project'] = project if reference == 'id' else 'hermes-agent'
    result = json.loads(registry.dispatch('kanban_create', args))
    assert result.get('ok'), result
    with kbc.connect() as conn:
        child = kb.get_task(conn, result['task_id'])
        assert child.project_id == project
        assert Path(child.workspace_path).parent == repo / '.worktrees'
        assert child.workspace_path != str(tmp_path / 'discovery')

    args['project'] = 'unregistered-project'
    with kbc.connect() as conn:
        before = conn.execute('SELECT count(*) FROM tasks').fetchone()[0]
    rejected = json.loads(registry.dispatch('kanban_create', args))
    assert not rejected.get('ok'), rejected
    with kbc.connect() as conn:
        assert conn.execute('SELECT count(*) FROM tasks').fetchone()[0] == before


@pytest.mark.parametrize('reference', ['id', 'slug'])
def test_worker_child_retains_creator_profile_project_anchor_for_explicit_parent_workspace(
    tmp_path, monkeypatch, reference,
):
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, projects_db as pdb

    shared_home = tmp_path / 'shared'
    creator_home = tmp_path / 'profiles' / 'creator'
    worker_home = tmp_path / 'profiles' / 'worker'
    repo = tmp_path / 'hermes-source'
    repo.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(shared_home))
    monkeypatch.setenv('HERMES_HOME', str(creator_home))
    monkeypatch.setenv('HERMES_PROFILE', 'creator')
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)

    with pdb.connect_closing() as conn:
        project = pdb.create_project(conn, name='Hermes', slug='hermes-agent', primary_path=str(repo))
    with kbc.connect() as conn:
        parent = kb.create_task(
            conn, title='Discovery', assignee='creator', project_id=project,
            workspace_kind='dir', workspace_path=str(tmp_path / 'discovery'),
        )
        parent_task = kb.get_task(conn, parent)
    assert parent_task.project_slug == 'hermes-agent'
    assert parent_task.project_repo == str(repo)

    monkeypatch.setenv('HERMES_HOME', str(worker_home))
    monkeypatch.setenv('HERMES_PROFILE', 'worker')
    with kbc.connect() as conn:
        child_id = kb.create_task(
            conn, title='Repair', assignee='worker',
            project_id=project if reference == 'id' else 'hermes-agent',
            project_source_task_id=parent,
        )
        child = kb.get_task(conn, child_id)
    assert child.project_id == project
    assert child.workspace_path == str(repo / '.worktrees' / child.id)
    assert child.branch_name == f'hermes-agent/{child.id}-repair'
