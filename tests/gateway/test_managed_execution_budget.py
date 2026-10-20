"""Owner deadlines and output ceilings hold even for silent/chatty workers."""
import asyncio
import time

import pytest

from hermes_state_runtime import RuntimeStoreError


def test_silent_worker_is_bounded():
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        budget = ExecutionBudget({'wall_seconds': 1, 'output_bytes': 1024, 'requests': 1})
        with pytest.raises(RuntimeStoreError, match='execution_wall_budget'):
            await budget.wait(asyncio.Event().wait())
    start = time.monotonic()
    asyncio.run(probe())
    assert time.monotonic() - start < 3


def test_stderr_overflow_wakes_owner_and_discards_further_output():
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        budget = ExecutionBudget({'wall_seconds': 2, 'output_bytes': 8, 'requests': 1})
        pending = asyncio.create_task(budget.wait(asyncio.Event().wait()))
        await asyncio.to_thread(budget.consume, 9)
        with pytest.raises(RuntimeStoreError, match='execution_output_budget'):
            await pending
        assert budget.consume(1) is False
    asyncio.run(probe())


def test_chatty_frames_cannot_renew_deadline():
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        budget = ExecutionBudget({'wall_seconds': 1, 'output_bytes': 65536, 'requests': 1})
        with pytest.raises(RuntimeStoreError, match='execution_wall_budget'):
            while True:
                await budget.wait(asyncio.sleep(.02))  # health: allow HX013 -- frames must continue across the fixed deadline
                budget.frame({'type': 'delta', 'text': 'hello'})
    asyncio.run(probe())


def test_deadline_closes_child_before_waiting_for_late_reservation():
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        loop = asyncio.get_running_loop()
        released = asyncio.Event()
        closed = []

        class Worker:
            def close(self):
                closed.append(True)
                loop.call_soon_threadsafe(released.set)

        async def commit():
            await released.wait()
            assert closed
            return {'execution_id': 'late'}
        budget = ExecutionBudget({'wall_seconds': 1, 'output_bytes': 1024, 'requests': 1})
        scope, error = await budget.reserve(Worker(), commit())
        assert scope == {'execution_id': 'late'}
        assert isinstance(error, RuntimeStoreError) and error.reason == 'execution_wall_budget'
    asyncio.run(probe())
