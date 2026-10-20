"""Bound managed-owner supervision; loss still follows the existing unknown/fence path."""
import asyncio
import contextvars
import threading
import time

from hermes_state_runtime import RuntimeStoreError
from hermes_cli.kanban_execution_budget import validate_budget


class ExecutionBudget:
    def __init__(self, value):
        self.value = None if value is None else validate_budget(value)
        self.deadline = None if value is None else time.monotonic() + self.value['wall_seconds']
        self._loop = asyncio.get_running_loop()
        self._overflow = asyncio.Event()
        self._lock = threading.Lock()
        self._bytes = 0

    def consume(self, size):
        """May run on the stderr thread; discard excess but keep draining the pipe."""
        if self.value is None:
            return True
        with self._lock:
            was_allowed = self._bytes <= self.value['output_bytes']
            self._bytes = min(self._bytes + size, self.value['output_bytes'] + 1)
            allowed = self._bytes <= self.value['output_bytes']
        if was_allowed and not allowed:
            self._loop.call_soon_threadsafe(self._overflow.set)
        return allowed

    def frame(self, frame):
        if self.value is None:
            return
        # Include protocol overhead and non-delta frames: no tool/result bypass.
        from agent.managed_worker import encode_frame
        size = len(encode_frame(frame))
        if not self.consume(size):
            raise RuntimeStoreError('execution_output_budget')
        self.check()

    def check(self, reservation_error=None):
        if reservation_error is not None:
            raise reservation_error
        with self._lock:
            overflow = self.value is not None and self._bytes > self.value['output_bytes']
        if overflow:
            raise RuntimeStoreError('execution_output_budget')
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise RuntimeStoreError('execution_wall_budget')

    async def wait(self, awaitable):
        if self.value is None:
            return await awaitable
        operation = asyncio.ensure_future(awaitable)
        overflow = asyncio.create_task(self._overflow.wait())
        try:
            self.check()
            done, _ = await asyncio.wait({operation, overflow},
                timeout=max(0, self.deadline - time.monotonic()), return_when=asyncio.FIRST_COMPLETED)
            self.check()
            if operation not in done:
                raise RuntimeStoreError('execution_wall_budget')
            return operation.result()
        finally:
            for task in (operation, overflow):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, overflow, return_exceptions=True)

    async def reserve(self, worker, awaitable):
        """Stop the owned child on deadline while retaining a late ledger commit's scope.

        The caller receives (scope, error) so it can revoke a committed reservation through
        its existing unknown/fence path. Never cancel a shielded write and discard its result.
        Ledger settlement may outlast the execution deadline; the child cannot execute then.
        """
        if self.value is None:
            return await awaitable, None
        reservation = asyncio.ensure_future(awaitable)
        try:
            return await self.wait(asyncio.shield(reservation)), None
        except (RuntimeStoreError, asyncio.CancelledError) as exc:
            await self._close_before_fence(worker)
            return await self._join_loss(reservation), exc

    async def close_exhausted(self, worker, error):
        """End local execution before any potentially contended loss/fencing write."""
        if isinstance(error, RuntimeStoreError) and error.reason in {
                'execution_wall_budget', 'execution_output_budget'}:
            await self._close_before_fence(worker)

    async def finish(self, worker):
        """Seal output accounting before publishing a bounded worker's terminal result."""
        if self.value is None:
            return
        await self._close_before_fence(worker)
        if worker.process.poll() is None or (worker.stderr_drain is not None and worker.stderr_drain.is_alive()):
            raise RuntimeStoreError('execution_output_unconfirmed')
        self.check()

    async def _close_before_fence(self, worker):
        """Join the same owner teardown despite cancellation; preserve the original loss."""
        cleanup = asyncio.get_running_loop().run_in_executor(None, contextvars.copy_context().run, worker.close)
        try:
            await self._join_loss(cleanup)
        except (OSError, RuntimeError, ValueError) as exc:
            # A pipe/signalling error cannot skip the original loss's unknown fence.
            # The owner's finally still retries this same lifecycle's teardown.
            import logging
            logging.getLogger(__name__).warning('Budget worker cleanup unconfirmed (%s)', type(exc).__name__)

    async def _join_loss(self, pending):
        """Recover a committed scope/teardown even if this caller is canceled meanwhile."""
        while True:
            try:
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                if pending.cancelled():
                    raise
