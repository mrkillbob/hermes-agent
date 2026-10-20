"""Operator controls for the existing drain's one exact Kanban claim exception."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from hermes_state_runtime import RuntimeStoreError


def _selected_target(args):
    from hermes_cli import kanban_db as kb
    from hermes_cli.profiles import resolve_profile_env
    from gateway.drain_kanban import _target
    path = kb.kanban_db_path(board=args.board).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        task = db.execute('SELECT id,assignee,status FROM tasks WHERE id=?', (args.task_id,)).fetchone()
        if task is None or task['status'] != 'ready' or not task['assignee']:
            raise RuntimeStoreError('kanban_window_requires_ready_assigned_task')
        if db.execute("SELECT 1 FROM tasks WHERE status='running' LIMIT 1").fetchone():
            raise RuntimeStoreError('kanban_window_requires_idle_board')
    return _target(dict(board=args.board, db=str(path), task_id=task['id'],
                        profile_id=str(Path(resolve_profile_env(task['assignee'])).resolve())))


def _status():
    from gateway.drain_kanban import state, _remaining
    _, value = state()
    return dict(window_id=value['window_id'], task_id=value['target']['task_id'], board=value['target']['board'],
                armed=value['owner_instance'] is not None, owner_instance=value['owner_instance'],
                claimed=value['claim'] is not None, cancelled=value['cancelled'],
                remaining_seconds=max(0, int(_remaining(value))), held=True)


def _begin(args):
    from gateway.drain_kanban import begin
    begin(_selected_target(args), seconds=args.seconds)
    return _status()


def _arm(args):
    from gateway.drain_kanban import arm, state, REASON
    from hermes_cli.gateway_runtime import discover_gateway_endpoint
    _, value = state()
    found = discover_gateway_endpoint(Path(value['target']['profile_id']))
    if found.state != 'draining' or found.reason_code != REASON or found.endpoint is None:
        raise RuntimeStoreError('kanban_window_requires_held_owner')
    arm(found.endpoint.instance_id, window_id=args.window_id)
    return _status()


def _cancel(args):
    from gateway.drain_kanban import cancel
    cancel(window_id=args.window_id)
    return _status()


def _restore(args):
    from gateway.drain_kanban import restore
    # The marker writer itself reserves canonical owner locks through restoration.
    restore(window_id=args.window_id, owner_stopped=True)
    return {'restored': True}


def execute(args):
    actions = {'begin': _begin, 'arm': _arm, 'status': lambda _: _status(), 'cancel': _cancel, 'restore': _restore}
    return actions[args.drain_action](args)


def cmd_gateway_drain(args):
    try:
        payload = execute(args)
    except (RuntimeError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({'error': getattr(exc, 'reason', 'kanban_window_refused')}))
        raise SystemExit(6) from exc
    print(json.dumps(payload, sort_keys=True))


def add_parser(subparsers):
    parser = subparsers.add_parser('drain', help='Hold all ingress for one exact bounded Kanban attempt')
    actions = parser.add_subparsers(dest='drain_action', required=True)
    begin = actions.add_parser('begin', help='Under ESTOP, hold an idle board and stopped canonical owners')
    begin.add_argument('--task-id', required=True)
    begin.add_argument('--board', default='default')
    begin.add_argument('--seconds', type=int, default=900)
    actions.add_parser('status', help='Read the hold and grant without exposing the prior marker')
    for action in ('arm', 'cancel', 'restore'):
        command = actions.add_parser(action)
        command.add_argument('--window-id', required=True)
    parser.set_defaults(func=cmd_gateway_drain)
