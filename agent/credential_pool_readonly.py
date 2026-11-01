"""Read-only credential-pool resolution for preparing an uninstalled runtime.

Use only with freshly loaded API-key pools. In-memory seeding/cooldown observations
remain available; no auth-store normalization is required merely to resolve a
candidate. Pools retain their token baselines for normal persistence after scope exit.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import get_ident

_SCOPE = ContextVar('readonly_pool_resolution', default=None)


@contextmanager
def readonly_pool_resolution():
    scope = [get_ident(), True]
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        scope[1] = False
        _SCOPE.reset(token)


def mark_resolution_pool(pool):
    pool._resolution_readonly_scope = _SCOPE.get()


def pool_resolution_is_readonly(pool):
    scope = _SCOPE.get()
    return (scope is not None and scope[1] and scope[0] == get_ident()
            and getattr(pool, '_resolution_readonly_scope', None) is scope)
