"""Graceful gateway exit releases its inference runtime after accepted work drains."""

import asyncio
import threading

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_fails', [False, True])
async def test_gateway_drains_before_stopping_owned_runtime(monkeypatch, stop_fails):
    from gateway import run_bootstrap, run_local_runtime, run_runtime
    from gateway.config import GatewayConfig
    from gateway.shutdown_watchdog import effective_shutdown_watchdog_delay
    from hermes_cli.local_runtime import bootstrap
    from hermes_constants import get_hermes_home

    get_hermes_home().chmod(0o700)
    calls = []
    owners = []
    budgets = []
    stop_runtime = run_local_runtime.stop_owned_local_runtime

    async def record_budget(timeout):
        budgets.append(timeout)
        await stop_runtime(timeout)

    class OwnedRuntime:
        def stop(self):
            calls.append('runtime-stopped')
            if stop_fails:
                raise RuntimeError('inert supervisor stop failure')

    async def finish_work(runner):
        owners.append(runner)
        assert calls == []
        calls.append('work-finished')

    monkeypatch.setattr(bootstrap, '_SUPERVISOR', OwnedRuntime())
    monkeypatch.setattr(run_runtime, 'wait_gateway_runtime', finish_work)
    monkeypatch.setattr(run_local_runtime, 'stop_owned_local_runtime', record_budget)
    await asyncio.wait_for(run_bootstrap.start_gateway(GatewayConfig()), 30)
    assert calls == ['work-finished', 'runtime-stopped']
    assert owners[0]._shutdown_event.is_set()
    assert budgets[0] <= effective_shutdown_watchdog_delay(owners[0]) - run_runtime.STOP_TAIL_RESERVE_SECONDS
    if not stop_fails:
        assert bootstrap.get_supervisor() is None


@pytest.mark.asyncio
async def test_blocked_native_stop_does_not_hold_the_event_loop_executor(monkeypatch):
    from gateway.run_local_runtime import stop_owned_local_runtime
    from hermes_cli.local_runtime import bootstrap

    release = threading.Event()
    entered = threading.Event()
    exited = threading.Event()
    workers = []

    class BlockedRuntime:
        def stop(self):
            workers.append(threading.current_thread())
            entered.set()
            try:
                release.wait(10)
            finally:
                exited.set()

    monkeypatch.setattr(bootstrap, '_SUPERVISOR', BlockedRuntime())
    try:
        with pytest.raises(TimeoutError, match='remaining shutdown budget'):
            await asyncio.wait_for(stop_owned_local_runtime(0), 5)
        assert await asyncio.to_thread(entered.wait, 5)
        assert workers[0].daemon
        # A blocked native stop must not occupy the executor asyncio.run joins on exit.
        assert await asyncio.wait_for(asyncio.to_thread(lambda: 'available'), 5) == 'available'
    finally:
        release.set()
        assert await asyncio.to_thread(exited.wait, 5)


@pytest.mark.asyncio
async def test_adopter_cleanup_does_not_inspect_or_stop_another_owner(monkeypatch):
    from gateway.run_local_runtime import stop_owned_local_runtime
    from hermes_cli.local_runtime import bootstrap

    calls = []
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(bootstrap, 'shutdown_local_runtime', lambda: calls.append('shutdown'))
    await stop_owned_local_runtime(0)
    assert calls == []
