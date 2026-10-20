"""Admission for a single managed worker's frozen provider request budget.

The owner retains wall/output/stop authority. HTTP hooks count physical attempts,
including SDK retries and redirects, across every client and helper thread in this
fresh exec. Closing admission says nothing about a remote provider's cancellation.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Callable


class RequestBudgetExhausted(InterruptedError):
    """No further provider dispatch is admitted for this worker execution."""


class _RequestBudget:
    def __init__(self, limit: int):
        self.pid = os.getpid()
        self.limit = limit
        self.used = 0
        self.stopped = False
        self.reason: str | None = None
        self.on_stop: Callable[[], None] | None = None
        self.lock = threading.Lock()


_budget: _RequestBudget | None = None
_bind_lock = threading.Lock()
_client_lock = threading.Lock()


def bind_worker_request_budget(frame: dict) -> None:
    """Private bootstrap only; never read mutable config or reset between turns.

    The owner validates the complete execution_budget contract. Validate the
    request ceiling again at this process boundary before importing providers.
    """
    global _budget
    context = json.loads(frame['policy'].get('kanban_json') or 'null')
    if context is None or 'execution_budget' not in context:
        return
    value = context['execution_budget']
    if not isinstance(value, dict):
        raise ValueError('invalid_worker_request_budget')
    limit = value.get('requests')
    if type(limit) is not int or not 1 <= limit <= 4:
        raise ValueError('invalid_worker_request_budget')
    with _bind_lock:
        if _budget is not None:
            raise RuntimeError('worker request budget already bound')
        _budget = _RequestBudget(limit)


def _current_budget() -> _RequestBudget | None:
    if _budget is not None and _budget.pid != os.getpid():
        raise RequestBudgetExhausted('worker_request_budget_crossed_fork')
    return _budget


def register_request_stop(callback: Callable[[], None]) -> None:
    """Use WorkerControls.stop for exhaustion, sharing its existing interrupt path."""
    budget = _current_budget()
    if budget is not None:
        with budget.lock:
            budget.on_stop = callback


def stop_provider_requests() -> None:
    budget = _current_budget()
    if budget is not None:
        with budget.lock:
            budget.stopped = True


def _refuse(budget: _RequestBudget, reason: str) -> None:
    with budget.lock:
        if budget.stopped and budget.reason is None:
            raise RequestBudgetExhausted('worker_request_admission_closed')
        budget.stopped = True
        if budget.reason is None:
            budget.reason = reason
        callback = budget.on_stop
    if callback is not None:
        callback()
    raise RequestBudgetExhausted(reason)


def stamp_request_budget_result(result):
    """Keep a swallowed SDK/recovery error from becoming a successful receipt."""
    budget = _current_budget()
    if budget is None or not isinstance(result, dict):
        return result
    with budget.lock:
        reason = budget.reason
    if reason is not None:
        result = {**result, 'interrupted': True, 'failed': True, 'completed': False,
                  'turn_exit_reason': reason, 'failure_reason': reason, 'error': reason}
    return result


def consume_provider_request() -> None:
    """Immediately before one physical send; failed attempts are never refunded."""
    budget = _current_budget()
    if budget is None:
        return
    with budget.lock:
        if budget.stopped:
            raise RequestBudgetExhausted(budget.reason or 'worker_request_admission_closed')
        admitted = budget.used < budget.limit
        if admitted:
            budget.used += 1
    if not admitted:
        _refuse(budget, 'worker_request_budget_exhausted')


def _request_hook(request) -> None:
    consume_provider_request()


async def _async_request_hook(request) -> None:
    consume_provider_request()


def prepare_provider_client(client):
    """Install one HTTP send gate, or refuse an uncounted transport when bounded.

    SDK retries re-enter httpx's request hooks. Instrumenting that boundary also
    avoids counting nested conversation/summary wrappers twice.
    """
    budget = _current_budget()
    if budget is None:
        return client
    import httpx

    http = getattr(client, '_client', None)
    if not isinstance(http, (httpx.Client, httpx.AsyncClient)):
        _refuse(budget, 'uncounted_provider_transport')
    hook = _async_request_hook if isinstance(http, httpx.AsyncClient) else _request_hook
    with _client_lock:
        hooks = http.event_hooks['request']
        if hook not in hooks:
            hooks.append(hook)
    return client


def require_counted_route(api_mode: str, provider: str) -> None:
    """Other adapters need their own physical boundary before bounded admission."""
    budget = _current_budget()
    if budget is not None and (api_mode != 'chat_completions' or provider == 'moa'):
        _refuse(budget, 'uncounted_provider_transport')
