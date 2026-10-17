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


async def stop_owned_local_runtime(timeout: float) -> None:
    """Attempt owned cleanup within the stop's remaining leash; never join a stuck worker."""
    from agent.memory_provider import spawn_context_thread
    from gateway.run import _await_thread_exit
    from hermes_cli.local_runtime.bootstrap import has_owned_runtime, shutdown_local_runtime

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
    thread.start()
    if not await _await_thread_exit(thread, timeout):
        raise TimeoutError('managed local runtime stop exceeded remaining shutdown budget')
    if errors:
        raise errors[0]
