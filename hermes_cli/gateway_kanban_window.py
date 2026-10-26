"""Operator controls for the existing drain's one exact Kanban claim exception."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from hermes_state_runtime import RuntimeStoreError


def _selected_target(args, path):
    from hermes_cli.profiles import resolve_profile_env
    from gateway.drain_kanban import _target
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
    from hermes_cli.startup_hold import read_marker, journal_present, REASON
    if journal_present() or REASON in (read_marker() or {}):
        from gateway.drain_startup import status
        return status()
    from gateway.drain_kanban import state, _remaining
    _, value = state()
    return dict(window_id=value['window_id'], task_id=value['target']['task_id'], board=value['target']['board'],
                armed=value['owner_instance'] is not None, owner_instance=value['owner_instance'],
                claimed=value['claim'] is not None, cancelled=value['cancelled'],
                remaining_seconds=max(0, int(_remaining(value))), held=True)


def _begin(args):
    from gateway.drain_kanban import begin
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import _dispatch_host_admission_lock, _dispatch_tick_lock
    path = kb.kanban_db_path(board=args.board).resolve()
    # Match dispatch's lock order and keep its admission fenced until publication.
    with _dispatch_host_admission_lock(strict=True) as host_acquired:
        if not host_acquired:
            raise RuntimeStoreError('kanban_window_dispatch_busy')
        with _dispatch_tick_lock(path, strict=True) as board_acquired:
            if not board_acquired:
                raise RuntimeStoreError('kanban_window_dispatch_busy')
            begin(_selected_target(args, path), seconds=args.seconds)
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
    if getattr(args, 'hold_id', None):
        from gateway.drain_startup import restore
        restore(hold_id=args.hold_id)
        return {'restored': True}
    from gateway.drain_kanban import restore
    # The marker writer itself reserves canonical owner locks through restoration.
    restore(window_id=args.window_id, owner_stopped=True)
    return {'restored': True}


def _hold(_args):
    from gateway.drain_startup import begin, status
    begin()
    return status()


def _reconcile(_args):
    from hermes_cli.startup_hold_reconcile import assess
    return assess()


_STARTUP_REFUSALS = frozenset({
    'startup_hold_writers_busy', 'startup_hold_writers_unknown',
    'startup_hold_requires_pause', 'startup_hold_ledger_changed',
})


def _refusal_reason(exc):
    reason = getattr(exc, 'reason', None)
    if reason is not None:
        return reason
    # Only exact, explicitly public codes may cross the plain RuntimeError seam.
    if (type(exc) is RuntimeError and len(exc.args) == 1
            and isinstance(exc.args[0], str) and exc.args[0] in _STARTUP_REFUSALS):
        return exc.args[0]
    return 'kanban_window_refused'


def execute(args):
    actions = {'reconcile': _reconcile, 'hold': _hold, 'begin': _begin, 'arm': _arm, 'status': lambda _: _status(), 'cancel': _cancel, 'restore': _restore}
    return actions[args.drain_action](args)


def cmd_gateway_drain(args):
    try:
        payload = execute(args)
    except (RuntimeError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({'error': _refusal_reason(exc)}))
        raise SystemExit(6) from exc
    print(json.dumps(payload, sort_keys=True))


def add_parser(subparsers):
    parser = subparsers.add_parser('drain', help='Hold all ingress for one exact bounded Kanban attempt')
    actions = parser.add_subparsers(dest='drain_action', required=True)
    begin = actions.add_parser('begin', help='Under ESTOP, hold an idle board and stopped canonical owners')
    begin.add_argument('--task-id', required=True)
    begin.add_argument('--board', default='default')
    begin.add_argument('--seconds', type=int, default=900)
    actions.add_parser('reconcile', help='Under ESTOP, assess legacy closure debt without changing the ledger')
    actions.add_parser('hold', help='Under ESTOP, hold native startup without selecting or admitting any task')
    actions.add_parser('status', help='Read the hold and grant without exposing the prior marker')
    for action in ('arm', 'cancel'):
        command = actions.add_parser(action)
        command.add_argument('--window-id', required=True)
    restore = actions.add_parser('restore')
    identity = restore.add_mutually_exclusive_group(required=True)
    identity.add_argument('--window-id')
    identity.add_argument('--hold-id')
    parser.set_defaults(func=cmd_gateway_drain)
