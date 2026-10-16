"""Graceful gateway exit releases its inference runtime after accepted work drains."""

import asyncio

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_fails', [False, True])
async def test_gateway_drains_before_stopping_owned_runtime(monkeypatch, stop_fails):
    from gateway import run_bootstrap, run_runtime
    from gateway.config import GatewayConfig
    from hermes_cli.local_runtime import bootstrap
    from hermes_constants import get_hermes_home

    get_hermes_home().chmod(0o700)
    calls = []
    owners = []

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
    await asyncio.wait_for(run_bootstrap.start_gateway(GatewayConfig()), 30)
    assert calls == ['work-finished', 'runtime-stopped']
    assert owners[0]._shutdown_event.is_set()
    if not stop_fails:
        assert bootstrap.get_supervisor() is None
