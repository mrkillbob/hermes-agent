"""Cancellation-safe handoff of a managed subprocess acquired off the owner loop."""
import asyncio
import threading

from hermes_state_runtime import RuntimeStoreError


async def acquire_process(spawn, *args, budget=None, **kwargs):
    """Return (handle, loss); the caller owns a returned handle even on deadline/cancel."""
    from gateway.session_managed_worker import ManagedWorker
    lock = threading.Lock()
    state = {'cancelled': False, 'process': None}

    def create():
        process = spawn(*args, **kwargs)
        with lock:
            cancelled = state['cancelled']
            if not cancelled:
                state['process'] = process
        if cancelled:
            ManagedWorker(process).close()
        return process

    pending = asyncio.create_task(asyncio.to_thread(create))
    try:
        # Keep the handle owned here until the budget accepts the handoff. An outer
        # deadline check could discard a child returned exactly as the deadline expires.
        process = await (asyncio.shield(pending) if budget is None else budget.wait(asyncio.shield(pending)))
        with lock:
            state['process'] = None
        return process, None
    except (asyncio.CancelledError, RuntimeStoreError) as exc:
        with lock:
            state['cancelled'] = True
            process = state['process']
            state['process'] = None
        if process is not None:
            # Let the existing owner finally retain teardown/retry ownership. Do not
            # hide a close error here and discard the handle before the owner sees it.
            return process, exc
        # If creation still runs, its thread owns cleanup; consume an eventual spawn exception.
        pending.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        raise
