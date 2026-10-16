"""Release process-owned inference without letting native cleanup wedge gateway exit."""

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


async def stop_owned_local_runtime(timeout: float) -> None:
    """Attempt owned cleanup within the stop's remaining leash; never join a stuck worker."""
    from agent.memory_provider import spawn_context_thread
    from hermes_cli.local_runtime.bootstrap import get_supervisor, shutdown_local_runtime

    if get_supervisor() is None:
        return
    finished = threading.Event()
    errors = []

    def stop() -> None:
        try:
            shutdown_local_runtime()
        except Exception as exc:
            logger.exception('Managed local runtime cleanup worker failed')
            errors.append(exc)
        finally:
            finished.set()

    # A default-executor worker would be joined by asyncio.run even after a wait timeout.
    spawn_context_thread(stop, name='gateway-local-runtime-stop').start()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    while not finished.is_set():
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError('managed local runtime stop exceeded remaining shutdown budget')
        await asyncio.sleep(min(0.05, remaining))
    if errors:
        raise errors[0]
