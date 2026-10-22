"""Federal discovery shares canonical intake ownership and its active-work guard."""
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db, projects_db
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_project_create import create_task
from scripts import federation_discovery as discovery


@pytest.mark.parametrize('already_owned', [False, True])
def test_federal_discovery_admits_only_unowned_canonical_intake(
    tmp_path, monkeypatch, capsys, already_owned,
):
    home = tmp_path / 'home'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    spec = json.loads((Path(discovery.__file__).resolve().parents[1] /
                       'configs/federation/discovery.json').read_text())
    board = spec.get('project_boards', {}).get('hermes-agent', spec['board'])
    with projects_db.connect_closing() as registry:
        project = projects_db.create_project(
            registry, name='Hermes Agent', slug='hermes-agent', board_slug=board,
        )
    monkeypatch.setattr(kanban_db, 'kanban_db_path', lambda name: tmp_path / (name + '.db'))
    for name in {spec['board'], *spec.get('project_boards', {}).values()}:
        with connect_closing(kanban_db.kanban_db_path(name)):
            pass
    with connect_closing(kanban_db.kanban_db_path(board)) as conn:
        if already_owned:
            kanban_db.create_task(conn, title='Existing intake', assignee='task-intake-router',
                                  project_id=project, board=board)

    def local_cli(_hermes, *args):
        destination = args[args.index('--board') + 1]
        with connect_closing(kanban_db.kanban_db_path(destination)) as conn:
            if 'list' in args:
                return [asdict(task) for task in kanban_db.list_tasks(conn)]
            task_id = create_task(
                conn, title=args[args.index('create') + 1], board=destination,
                assignee=args[args.index('--assignee') + 1],
                project_id=args[args.index('--project') + 1],
                require_active_project='--require-active-project' in args,
                created_by=args[args.index('--created-by') + 1],
                idempotency_key=args[args.index('--idempotency-key') + 1],
            )
            return asdict(kanban_db.get_task(conn, task_id))

    monkeypatch.setattr(discovery, 'run', local_cli)
    monkeypatch.setattr(discovery.sys, 'argv', ['discovery', '--hermes', 'fixture-cli', '--apply'])
    discovery.main()
    capsys.readouterr()
    with connect_closing(kanban_db.kanban_db_path(board)) as conn:
        tasks = [task for task in kanban_db.list_tasks(conn)
                 if task.created_by == 'federation-discovery-federal_core'
                 or task.title == 'Existing intake']
    assert len(tasks) == 1
    assert tasks[0].assignee == 'task-intake-router'
    assert tasks[0].project_id == project
    assert tasks[0].created_by == (None if already_owned else 'federation-discovery-federal_core')
