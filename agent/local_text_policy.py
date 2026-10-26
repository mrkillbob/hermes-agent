"""Per-admission restrictions; the private worker snapshot also covers raw threads."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from threading import get_ident

_restricted = ContextVar('local_text_only', default=False)
_primary = ContextVar('local_text_primary_resolution', default=None)


class LocalTextRestriction(RuntimeError):
    pass


def local_text_only():
    from agent.safe_worker_policy import local_text_worker_enabled
    return _restricted.get() or local_text_worker_enabled()


@contextmanager
def local_text_scope(enabled):
    token = _restricted.set(enabled is True or _restricted.get())
    try:
        yield
    finally:
        _restricted.reset(token)


@contextmanager
def primary_client_scope():
    lease = [get_ident(), True]
    token = _primary.set(lease)
    try:
        yield
    finally:
        lease[1] = False
        _primary.reset(token)


def require_auxiliary_allowed():
    if local_text_only():
        raise LocalTextRestriction('local_text_auxiliary_denied')


def require_client_allowed():
    lease = _primary.get()
    permitted = lease is not None and lease[0] == get_ident() and lease[1]
    if local_text_only() and not permitted:
        raise LocalTextRestriction('local_text_auxiliary_denied')


def require_tools_allowed():
    if local_text_only():
        raise LocalTextRestriction('local_text_tools_denied')


def require_metadata_allowed():
    if local_text_only():
        raise LocalTextRestriction('local_text_metadata_denied')


def environment_probe_enabled(agent):
    return not local_text_only() and getattr(agent, '_environment_probe', True)


def restricted_policy(policy):
    """Read only the frozen claim; configuration and environment cannot enable it."""
    field = policy.get if isinstance(policy, dict) else lambda key, default=None: getattr(policy, key, default)
    context = json.loads(field('kanban_json') or 'null')
    budget = context.get('execution_budget') if isinstance(context, dict) else None
    if not isinstance(budget, dict) or 'local_text_only' not in budget:
        return False
    from hermes_cli.kanban_execution_budget import validate_budget
    validate_budget(budget)
    if (field('source') != 'kanban' or field('safe_mode') is not True
            or field('ignore_user_config') is not True or field('toolsets') not in ([], ())):
        raise ValueError('invalid_local_text_policy')
    return True
