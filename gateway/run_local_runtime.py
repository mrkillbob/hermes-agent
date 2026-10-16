"""Release process-owned inference without letting native cleanup wedge gateway exit."""

import logging

logger = logging.getLogger(__name__)


async def stop_owned_local_runtime(timeout: float) -> None:
    """Attempt owned cleanup within the stop's remaining leash; never join a stuck worker."""
    from agent.memory_provider import spawn_context_thread
    from gateway.run import _await_thread_exit
    from hermes_cli.local_runtime.bootstrap import get_supervisor, shutdown_local_runtime

    if get_supervisor() is None:
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
