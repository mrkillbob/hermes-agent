"""Operator controls prepare a held owner without editing task or job policy."""
import argparse
from contextlib import closing
import json

import pytest


def parse(*argv):
    from hermes_cli.subcommands.gateway import build_gateway_parser
    parser = argparse.ArgumentParser()
    build_gateway_parser(parser.add_subparsers(), cmd_gateway=lambda _: None,
                         cmd_proxy=lambda _: None, cmd_gateway_enroll=lambda _: None)
    return parser.parse_args(['gateway', 'drain', *argv])


def test_begin_reads_exact_assignee_without_changing_board(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    kb.create_board('owned')
    with closing(connect(board='owned')) as db:
        tid = kb.create_task(db, title='untouched', assignee='default')
        before = tuple(db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone())
    (tmp_path / 'ESTOP').write_text('hold')
    args = parse('begin', '--board', 'owned', '--task-id', tid)
    from hermes_cli.gateway_kanban_window import execute
    result = execute(args)
    assert result['task_id'] == tid and not result['armed']
    assert 'previous_drain' not in json.dumps(result)
    with closing(connect(board='owned')) as db:
        assert tuple(db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()) == before
    restored = execute(parse('restore', '--window-id', result['window_id']))
    assert restored == {'restored': True}
    assert not (tmp_path / '.drain_request.json').exists()


def test_begin_refuses_unassigned_or_running_card(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    kb.create_board('owned')
    with closing(connect(board='owned')) as db:
        tid = kb.create_task(db, title='unassigned')
    (tmp_path / 'ESTOP').write_text('hold')
    from hermes_cli.gateway_kanban_window import execute
    with pytest.raises((ValueError, RuntimeError)):
        execute(parse('begin', '--board', 'owned', '--task-id', tid))
    assert not (tmp_path / '.drain_request.json').exists()


@pytest.mark.parametrize('dry_run', [False, True])
def test_held_broad_dispatch_preserves_unrelated_board_state(tmp_path, monkeypatch, dry_run):
    from hermes_cli import kanban_db as kb, kanban_db_dispatch as dispatch
    from hermes_cli.kanban_db_connect import connect
    from gateway import drain_kanban as window
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    kb.create_board('owned')
    with closing(connect(board='owned')) as db:
        selected = kb.create_task(db, title='selected', assignee='default')
        other = kb.create_task(db, title='must remain staged', assignee='default')
        db.execute("UPDATE tasks SET status='todo' WHERE id=?", (other,))
        db.commit()
        before = {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                  for table in ('tasks', 'task_events', 'task_runs')}
        (tmp_path / 'ESTOP').write_text('hold')
        window.begin(dict(board='owned', db=str(kb.kanban_db_path(board='owned')),
                          task_id=selected, profile_id=str(tmp_path)))
        result = dispatch.dispatch_once(db, board='owned', dry_run=dry_run, max_spawn=1)
        after = {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                 for table in before}
        assert after == before
        assert result.paused and not result.spawned
