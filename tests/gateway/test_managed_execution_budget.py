"""Owner deadlines and output ceilings hold even for silent/chatty workers."""
import asyncio
import time

import pytest

from hermes_state_runtime import RuntimeStoreError


@pytest.mark.parametrize('text', ['hello', '\U0001f600' * 20])
def test_protocol_ceiling_counts_exact_wire_encoding(text):
    from agent.managed_worker import encode_frame
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        frame = {'type': 'delta', 'text': text}
        size = len(encode_frame(frame))
        exact = ExecutionBudget({'wall_seconds': 2, 'output_bytes': size, 'requests': 1})
        exact.frame(frame)
        too_small = ExecutionBudget({'wall_seconds': 2, 'output_bytes': size - 1, 'requests': 1})
        with pytest.raises(RuntimeStoreError, match='execution_output_budget'):
            too_small.frame(frame)
    asyncio.run(probe())


def test_terminal_result_refuses_unconfirmed_stderr_drain():
    from types import SimpleNamespace
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        worker = SimpleNamespace(close=lambda: None, process=SimpleNamespace(poll=lambda: 0),
                                 stderr_drain=SimpleNamespace(is_alive=lambda: True))
        budget = ExecutionBudget({'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1})
        with pytest.raises(RuntimeStoreError, match='execution_output_unconfirmed'):
            await budget.finish(worker)
    asyncio.run(probe())


def test_silent_worker_is_bounded():
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        budget = ExecutionBudget({'wall_seconds': 1, 'output_bytes': 1024, 'requests': 1})
        with pytest.raises(RuntimeStoreError, match='execution_wall_budget'):
            await budget.wait(asyncio.Event().wait())
    start = time.monotonic()
    asyncio.run(probe())
    assert time.monotonic() - start < 3


def test_owner_cancellation_cannot_abandon_pre_fence_cleanup():
    import threading
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        entered, release = threading.Event(), threading.Event()
        closed = []

        class Worker:
            def close(self):
                entered.set()
                assert release.wait(5)
                closed.append(True)
        budget = ExecutionBudget({'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1})
        task = asyncio.create_task(budget.close_exhausted(Worker(), RuntimeStoreError('execution_wall_budget')))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        await asyncio.wait_for(task, 2)
        assert closed == [True]
    asyncio.run(probe())


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


@pytest.mark.parametrize('close_error', [False, True])
def test_deadline_closes_child_before_waiting_for_late_reservation(close_error):
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        loop = asyncio.get_running_loop()
        released = asyncio.Event()
        closed = []

        class Worker:
            def close(self):
                closed.append(True)
                loop.call_soon_threadsafe(released.set)
                if close_error:
                    raise BrokenPipeError('fixture closed pipe')

        async def commit():
            await released.wait()
            assert closed
            return {'execution_id': 'late'}
        budget = ExecutionBudget({'wall_seconds': 1, 'output_bytes': 1024, 'requests': 1})
        scope, error = await budget.reserve(Worker(), commit())
        assert scope == {'execution_id': 'late'}
        assert isinstance(error, RuntimeStoreError) and error.reason == 'execution_wall_budget'
    asyncio.run(probe())


def test_cancellation_after_close_still_recovers_late_reservation():
    from gateway.session_execution_budget import ExecutionBudget

    async def probe():
        closed, commit = asyncio.Event(), asyncio.Event()
        budget = ExecutionBudget({'wall_seconds': 1, 'output_bytes': 1024, 'requests': 1})
        original_close = budget._close_before_fence

        async def close(worker):
            await original_close(worker)
            closed.set()
        budget._close_before_fence = close

        class Worker:
            def close(self):
                pass

        async def reservation():
            await commit.wait()
            return {'execution_id': 'late'}
        task = asyncio.create_task(budget.reserve(Worker(), reservation()))
        try:
            await asyncio.wait_for(closed.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            commit.set()
        scope, error = await asyncio.wait_for(task, 2)
        assert scope == {'execution_id': 'late'} and error.reason == 'execution_wall_budget'
    asyncio.run(probe())
