"""One owner/claim exception to an external drain; the hold outlives permission.

This extends the existing drain marker and dispatcher locks. It never edits a job,
starts a runtime, lifts ESTOP, or owns worker cancellation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, UTC
from pathlib import Path
import time
import math
import uuid

from hermes_constants import get_default_hermes_root
from hermes_state_runtime import RuntimeStoreError

REASON = 'kanban_once'
_TARGET_KEYS = {'board', 'db', 'task_id', 'profile_id'}
_CLAIM_KEYS = _TARGET_KEYS | {'run_id', 'claim_lock', 'execution_budget'}
_scope = ContextVar('kanban_drain_claim', default=None)


def marker_path():
    from gateway.drain_control import drain_request_path
    return drain_request_path(get_default_hermes_root())


def body():
    from gateway.drain_control import read_drain_request
    return read_drain_request(home=get_default_hermes_root())


def held(runner=None):
    """A booted held owner cannot reopen if its marker disappears or is replaced."""
    from hermes_cli.startup_hold import active
    if bool(getattr(runner, '_kanban_drain', False)) or active(runner):
        return True
    current = body()
    if current is not None and 'kanban_once' in current:
        return True
    try:
        return marker_path().exists() and not current  # unreadable/corrupt holds, never grants
    except OSError:
        return True


def state():
    current = body() or {}
    value = current.get('kanban_once')
    keys = {'window_id', 'target', 'expires_at', 'expires_monotonic', 'owner_instance', 'claim', 'previous_drain', 'cancelled'}
    try:
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError('shape')
        if not isinstance(value['window_id'], str) or not value['window_id']:
            raise ValueError('identity')
        if any(type(value[k]) not in (int, float) or not math.isfinite(value[k]) for k in ('expires_at', 'expires_monotonic')):
            raise ValueError('deadline')
        if value['target'] != _target(value['target']) or type(value['cancelled']) is not bool:
            raise ValueError('target')
        if any(value[k] is not None and (not isinstance(value[k], str) or not value[k]) for k in ('owner_instance',)):
            raise ValueError('owner')
        if value['previous_drain'] is not None and not isinstance(value['previous_drain'], str):
            raise ValueError('previous')
        if value['claim'] is not None and value['claim'] != _claim(value['claim']):
            raise ValueError('claim')
    except (ValueError, TypeError, KeyError, RuntimeStoreError) as exc:
        raise RuntimeStoreError('kanban_window_unavailable') from exc
    return current, value


def _remaining(value):
    return min(value['expires_at'] - time.time(), value['expires_monotonic'] - time.monotonic())


@contextmanager
def _marker_lock():
    from gateway.drain_control import drain_marker_lock
    with drain_marker_lock(home=get_default_hermes_root()):
        yield


@contextmanager
def _quiescent_profiles():
    from gateway.runtime_ownership import exclusive_maintenance
    from hermes_cli.profiles import list_profiles
    homes = {get_default_hermes_root(), *(Path(p.path) for p in list_profiles())}
    with exclusive_maintenance(homes, purpose='kanban-window'):
        yield


def _save(current):
    from utils import atomic_json_write
    atomic_json_write(marker_path(), current, mode=0o600, fsync_dir=True)


def _target(value):
    if (not isinstance(value, dict) or set(value) != _TARGET_KEYS
            or any(not isinstance(v, str) or not v for v in value.values())):
        raise RuntimeStoreError('invalid_params')
    from hermes_cli.kanban_dispatch_tick import normalize_task_ids
    from hermes_cli.kanban_db import _require_slug
    try:
        normalize_task_ids([value['task_id']])
        _require_slug(value['board'])
    except ValueError as exc:
        raise RuntimeStoreError('invalid_params') from exc
    return dict(value, db=str(Path(value['db']).resolve()), profile_id=str(Path(value['profile_id']).resolve()))


def begin(target, *, seconds=900):
    """Install a hold while paused; a later explicit arm binds the actual owner instance."""
    from agent.estop import is_engaged
    if not is_engaged() or type(seconds) is not int or not 0 < seconds <= 900:
        raise RuntimeStoreError('kanban_window_requires_pause')
    target = _target(target)
    with _marker_lock(), _quiescent_profiles():
        previous = marker_path().read_bytes().decode('utf-8') if marker_path().exists() else None
        if held():
            raise RuntimeStoreError('kanban_window_exists')
        value = {'window_id': uuid.uuid4().hex, 'target': target, 'expires_at': time.time() + seconds,
                 'expires_monotonic': time.monotonic() + seconds, 'cancelled': False,
                 'owner_instance': None, 'claim': None, 'previous_drain': previous}
        _save({'action': 'drain', 'principal': 'kanban-once', 'requested_at': datetime.now(UTC).isoformat(),
               'kanban_once': value})
    return value


def arm(instance_id, *, window_id):
    """Only the operator command arms; a restart cannot inherit the old instance's grant."""
    from agent.estop import is_engaged
    if not is_engaged() or not isinstance(instance_id, str) or not instance_id:
        raise RuntimeStoreError('kanban_window_requires_pause')
    with _marker_lock():
        current, value = state()
        if (value['window_id'] != window_id or value.get('owner_instance') is not None
                or value.get('claim') is not None or value['cancelled'] or _remaining(value) <= 0):
            raise RuntimeStoreError('kanban_window_unavailable')
        value['owner_instance'] = instance_id
        _save(current)


def _claim(value):
    from hermes_cli.kanban_execution_budget import validate_budget
    if (not isinstance(value, dict) or set(value) != _CLAIM_KEYS or type(value.get('run_id')) is not int
            or value['run_id'] <= 0 or not isinstance(value.get('claim_lock'), str) or not value['claim_lock']):
        raise RuntimeStoreError('invalid_params')
    target = _target({k: value[k] for k in _TARGET_KEYS})
    try:
        budget = validate_budget(value['execution_budget'])
    except ValueError as exc:
        raise RuntimeStoreError('invalid_params') from exc
    return dict(target, run_id=value['run_id'], claim_lock=value['claim_lock'], execution_budget=budget)


def bind_claim(claim):
    """The existing dispatcher binds its committed claim before launching its submitter."""
    claim = _claim(claim)
    with _marker_lock():
        current, value = state()
        if (not value.get('owner_instance') or value['cancelled'] or _remaining(value) <= claim['execution_budget']['wall_seconds']
                or {k: claim[k] for k in _TARGET_KEYS} != value.get('target')
                or value.get('claim') is not None):
            raise RuntimeStoreError('kanban_window_unavailable')
        value['claim'] = claim
        _save(current)


def permitted(claim, *, instance_id):
    try:
        _, value = state()
        return (value.get('owner_instance') == instance_id and not value['cancelled'] and _remaining(value) > 0
                and value.get('claim') == _claim(claim))
    except (RuntimeStoreError, OSError, ValueError, TypeError, KeyError):
        return False


def latch(runner):
    """Before runtime recovery: retain the hold through all owner startup/exit paths."""
    if held():
        from hermes_cli.startup_hold import active
        runner._startup_hold = active(runner)
        runner._kanban_drain = True
        runner._external_drain_active = True
        runner._kanban_drain_session = None
        try:
            _, value = state()
            runner._kanban_drain_id = value['window_id']
        except RuntimeStoreError:
            runner._kanban_drain_id = None


@contextmanager
def claim_scope(connection, params, session_id):
    if not held(getattr(connection.authority, 'runner', None)):
        yield
        return
    authority = connection.authority
    _, value = state()
    target = value.get('target') or {}
    claim = {k: params.get(k) for k in ('board', 'task_id', 'run_id', 'claim_lock', 'execution_budget')}
    db = params.get('db', target.get('db'))
    if not isinstance(db, str) or not db:
        raise RuntimeStoreError('invalid_params')
    claim.update(db=str(Path(db).resolve()), profile_id=authority.profile_id)
    if (not connection.native_owner or not permitted(claim, instance_id=authority.instance_id)
            or not getattr(authority.runner, '_kanban_drain', False)
            or value['window_id'] != getattr(authority.runner, '_kanban_drain_id', None)):
        raise RuntimeStoreError('kanban_window_unavailable')
    _pin_control(connection, session_id, claim)
    scope = (authority.runner, authority.profile_id, session_id, claim)
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)


def admission_allowed(authority, session_id):
    from agent.estop import is_engaged
    scope = _scope.get()
    return (scope is not None and scope[:3] == (authority.runner, authority.profile_id, session_id)
            and not is_engaged() and permitted(scope[3], instance_id=authority.instance_id))


def method_allowed(connection, method, params):
    from hermes_cli.startup_hold import active
    if active(connection.authority.runner):
        return method in {'ping', 'runtime.describe'}
    if method in {'ping', 'runtime.describe'}:
        return True
    if method in {'worker.adopt', 'worker.persist'}:
        return _worker_method_allowed(connection, params)
    if method == 'kanban.run':
        return connection.native_owner is True and not getattr(connection.authority.runner, '_draining', False)
    return (method in {'prompt.receipt', 'session.events.since', 'session.interrupt'}
            and getattr(connection.authority.runner, '_kanban_drain_session', None)
            == (connection.authority.profile_id, params.get('session_id')))


def cancel(*, window_id):
    """Revoke only this grant; the existing drain watcher owns interruption."""
    with _marker_lock():
        current, value = state()
        if value['window_id'] != window_id:
            raise RuntimeStoreError('kanban_window_unavailable')
        value['cancelled'] = True
        _save(current)


def restore(*, window_id, owner_stopped):
    """Restore only the original marker, under pause, after the operator proves owner exit."""
    from agent.estop import is_engaged
    if not is_engaged() or owner_stopped is not True:
        raise RuntimeStoreError('kanban_window_requires_pause')
    with _marker_lock(), _quiescent_profiles():
        _, value = state()
        if value['window_id'] != window_id:
            raise RuntimeStoreError('kanban_window_unavailable')
        previous = value.get('previous_drain')
        if previous is None:
            marker_path().unlink()
        else:
            from utils import atomic_write_bytes
            atomic_write_bytes(marker_path(), previous.encode('utf-8'), mode=0o600, fsync_dir=True)


def dispatch_allowed(conn, *, board, task_ids, budget, max_spawn):
    """Every dispatch surface shares the existing host/board locks and this exact selection gate."""
    if not held():
        return True
    try:
        from hermes_cli.kanban_execution_budget import validate_budget
        from hermes_cli.gateway_runtime import discover_gateway_endpoint
        _, value = state()
        target = value['target']
        from hermes_cli.kanban_db import _lifecycle_board
        selected_board = _lifecycle_board(conn, board)
        path = next(row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main')
        found = discover_gateway_endpoint(Path(target['profile_id']))
        return (task_ids == (target['task_id'],) and selected_board == target['board']
                and str(Path(path).resolve()) == target['db'] and max_spawn == 1
                and validate_budget(budget) and value['claim'] is None
                and found.state == 'draining' and found.reason_code == REASON and found.endpoint is not None
                and found.endpoint.instance_id == value['owner_instance']
                and not value['cancelled'] and _remaining(value) > budget['wall_seconds']) is True
    except (RuntimeStoreError, OSError, ValueError, TypeError, KeyError, StopIteration):
        return False


def bind_task(task, budget, board):
    if not held():
        return
    from hermes_cli.kanban_db import kanban_db_path, get_current_board
    from hermes_cli.profiles import resolve_profile_env
    selected_board = board or get_current_board()
    bind_claim({'board': selected_board, 'db': str(kanban_db_path(board=selected_board).resolve()),
                'task_id': task.id, 'profile_id': str(Path(resolve_profile_env(task.assignee)).resolve()),
                'run_id': task.current_run_id, 'claim_lock': task.claim_lock, 'execution_budget': budget})


async def enforce_claim(runner):
    """The existing drain watcher asks the existing Stop owner to revoke a lost permission."""
    selected = getattr(runner, '_kanban_drain_control', None)
    if selected is None:
        return
    authority, actor, ref, claim = selected
    if permitted(claim, instance_id=authority.instance_id):
        return
    generation = authority.executing.get(ref.session_id)
    if generation is not None:
        try:
            await authority.interrupt(actor, ref, generation)
        except RuntimeStoreError as exc:
            if exc.reason not in {'stale_generation', 'not_found'}:
                raise


def connection_method_refused(connection, method, params):
    runner = getattr(connection.authority, 'runner', None)
    if held(runner):
        return not method_allowed(connection, method, params)
    from gateway.drain_control import ws_method_refused_while_draining
    return connection._operator is True and ws_method_refused_while_draining(runner, method)


def control_handlers(runner, handlers):
    """A held owner exposes identity and its existing private ticket issuer only."""
    return {key: handler for key, handler in handlers.items() if not held(runner) or key == 'identify'}


def _worker_method_allowed(connection, params):
    authority = connection.authority
    selected = getattr(authority.runner, '_kanban_drain_control', None)
    if selected is None or selected[0] is not authority or 'worker:adopt' not in connection.actor.capabilities:
        return False
    worker = getattr(authority, '_managed_workers', {}).get(selected[2].session_id)
    # Only the already-reserved child can adopt/persist. Existing worker verification still
    # checks its secret, durable execution, generation and physical compression target.
    return (worker is not None and getattr(worker, 'worker', None) == (params.get('pid'), params.get('birth'))
            and params.get('profile_id') == authority.profile_id)


def _pin_control(connection, session_id, claim):
    from gateway.session_contract import SessionRef
    authority, actor = connection.authority, connection.actor
    selected = getattr(authority.runner, '_kanban_drain_control', None)
    if selected is not None:
        if (selected[0] is not authority or selected[1].subject != actor.subject
                or selected[2].session_id != session_id or selected[3] != claim):
            raise RuntimeStoreError('kanban_window_unavailable')
        return
    authority.runner._kanban_drain_session = (authority.profile_id, session_id)
    authority.runner._kanban_drain_control = (authority, actor, SessionRef(authority.profile_id, session_id), claim)
