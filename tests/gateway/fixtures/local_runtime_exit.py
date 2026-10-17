"""Disposable exit probe: no inference engine, child server or network listener."""

import asyncio
import threading

from gateway.run_local_runtime import stop_owned_local_runtime
from hermes_cli.local_runtime import bootstrap

entered = threading.Event()


class BlockedRuntime:
    def stop(self):
        entered.set()
        threading.Event().wait()


bootstrap._SUPERVISOR = BlockedRuntime()
bootstrap._stop_at_exit()


async def stop():
    try:
        await stop_owned_local_runtime(0)
    except TimeoutError:
        pass
    assert entered.wait(5)


asyncio.run(stop())
print('gateway stop returned', flush=True)
