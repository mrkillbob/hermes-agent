"""Pin only explicit HOST-selected idle cached parents, under the existing cache lock.

Selectors read pins while holding that same lock. No broker lock is acquired from
inside the cache lock; publication order is broker -> cache, never the reverse.
"""
from gateway.session_learning_execution import _deny


def pin_parent(execution):
    from gateway.run_agent_cache import _first_agent
    runner = execution.authority.runner
    lock, cache = getattr(runner, '_agent_cache_lock', None), getattr(runner, '_agent_cache', None)
    if lock is None or cache is None:
        _deny()
    with lock:
        if (_first_agent(cache.get(execution.route)) is not execution.parent
                or (getattr(runner, '_running_agents', {}) or {}).get(execution.route) is not None):
            _deny('session_busy')
        if not hasattr(runner, '_reference_parent_pins'):
            runner._reference_parent_pins = {}
        runner._reference_parent_pins[id(execution)] = execution.parent


def parent_pinned(runner, agent):
    """Called ONLY inside the existing selector's cache lock, no other lock acquisition."""
    return any(parent is agent for parent in getattr(runner, '_reference_parent_pins', {}).values())


def release_pin(execution):
    if outstanding_work(execution):
        return
    runner = execution.authority.runner
    lock = getattr(runner, '_agent_cache_lock', None)
    if lock is not None:
        with lock:
            getattr(runner, '_reference_parent_pins', {}).pop(id(execution), None)
    if execution.status in {'revoked', 'unknown', 'settled'}:
        execution.parent, execution.history, execution.witness = None, [], None


def outstanding_work(execution):
    permit, send = execution.selected.admission, execution.native_send
    return bool((permit is not None and permit.active) or execution.worker_gate.locked()
                or (send is not None and not send._physical_done.is_set()))


def retire_preparation(selected):
    execution = selected.execution
    if execution is None:
        return
    with execution.broker._gate:
        execution.status, execution.history, selected.reference = 'revoked', [], None
        if not outstanding_work(execution):
            release_pin(execution)
            if selected.admission is None:
                getattr(execution.authority, '_reference_intents', {}).pop(
                    (id(execution.broker), execution.decision_id), None)


def retire_native_intents(authority, connection=None):
    """Native lifecycle cleanup; called only after the participating gates join."""
    for execution in list(getattr(authority, '_reference_intents', {}).values()):
        if connection is None or execution.selected.connection is connection:
            retire_preparation(execution.selected)
