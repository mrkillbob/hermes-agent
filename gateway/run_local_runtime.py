"""Release process-owned inference without letting native cleanup wedge gateway exit."""

import logging

logger = logging.getLogger(__name__)


def start_owned_local_runtime(runner, *, idle_exit: bool) -> None:
    """An idle-exit/consumer gateway cannot carry other processes' inference lifetime."""
    if idle_exit:
        return
    from hermes_cli.local_runtime.ownership import start_gateway_owner

    runner._local_runtime_owner = start_gateway_owner()


def begin_local_runtime_shutdown(runner) -> None:
    owner = getattr(runner, '_local_runtime_owner', None)
    if owner is not None:
        owner.close()


async def finalize_local_runtime(runner, ctx) -> None:
    """Hard exit skips atexit: release inference before dropping the gateway lock."""
    from gateway.run_runtime import STOP_TAIL_RESERVE_SECONDS
    from gateway.shutdown_watchdog import effective_shutdown_watchdog_delay

    try:
        await stop_owned_local_runtime(
            effective_shutdown_watchdog_delay(runner) - ctx.elapsed() - STOP_TAIL_RESERVE_SECONDS)
    except Exception:
        logger.warning('Managed local runtime shutdown failed', exc_info=True)


async def stop_owned_local_runtime(timeout: float) -> None:
    """Attempt owned cleanup within the stop's remaining leash; never join a stuck worker."""
    from agent.memory_provider import spawn_context_thread
    from gateway.run import _await_thread_exit
    from hermes_cli.local_runtime.bootstrap import (
        has_owned_runtime, set_exit_cleanup_delegated, shutdown_local_runtime,
    )

    if not has_owned_runtime():
        return
    errors = []

    def stop() -> None:
        try:
            shutdown_local_runtime()
        except Exception as exc:
            logger.exception('Managed local runtime cleanup worker failed')
            errors.append(exc)

    # A default-executor worker would be joined by asyncio.run even after a wait timeout.
    thread = spawn_context_thread(stop, name='gateway-local-runtime-stop')
    previous = set_exit_cleanup_delegated(True)
    try:
        thread.start()
    except BaseException:
        set_exit_cleanup_delegated(previous)
        raise
    if not await _await_thread_exit(thread, timeout):
        raise TimeoutError('managed local runtime stop exceeded remaining shutdown budget')
    if errors:
        raise errors[0]
