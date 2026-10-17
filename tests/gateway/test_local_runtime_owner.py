"""The durable root gateway owns inference across its served consumer work."""

import asyncio
import threading

import pytest


@pytest.mark.asyncio
async def test_gateway_boots_enabled_runtime_and_releases_it_after_work(monkeypatch):
    from gateway import run_bootstrap, run_runtime
    from gateway.config import GatewayConfig
    from hermes_cli.local_runtime import bootstrap
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    home.chmod(0o700)
    (home / 'config.yaml').write_text('local_runtime:\n  enabled: true\n  backend: metal\n')
    booted = threading.Event()
    calls = []
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)

    class OwnedRuntime:
        def stop(self):
            calls.append(('release',))

    def boot(config, force=False, *, owner=None):
        assert owner is not None and owner.home == home
        calls.append(('boot', get_hermes_home(), config['local_runtime']['enabled']))
        bootstrap._SUPERVISOR = OwnedRuntime()
        booted.set()
        return bootstrap._SUPERVISOR

    async def client_lifetime(runner):
        assert await asyncio.to_thread(booted.wait, 5), 'enabled gateway never started its model runtime'
        # Returning from one consumer does not run the host's cleanup.
        calls.append(('first-client-finished',))
        assert not any(call[0] == 'release' for call in calls)
        calls.append(('all-work-finished',))

    monkeypatch.setattr(bootstrap, 'ensure_local_runtime', boot)
    monkeypatch.setattr(run_runtime, 'wait_gateway_runtime', client_lifetime)
    await asyncio.wait_for(run_bootstrap.start_gateway(GatewayConfig()), 30)
    assert calls[0] == ('boot', home, True)
    assert [call[0] for call in calls] == [
        'boot', 'first-client-finished', 'all-work-finished', 'release',
    ]


@pytest.mark.asyncio
async def test_gateway_drains_before_stopping_its_owned_runtime(monkeypatch):
    from gateway import run_bootstrap, run_runtime
    from gateway.config import GatewayConfig
    from hermes_cli.local_runtime import bootstrap
    from hermes_constants import get_hermes_home

    get_hermes_home().chmod(0o700)
    calls = []

    class OwnedRuntime:
        def stop(self):
            calls.append('runtime-stopped')

    async def finish_work(runner):
        assert calls == []
        calls.append('work-finished')

    monkeypatch.setattr(bootstrap, '_SUPERVISOR', OwnedRuntime())
    monkeypatch.setattr(run_runtime, 'wait_gateway_runtime', finish_work)
    await asyncio.wait_for(run_bootstrap.start_gateway(GatewayConfig()), 30)
    assert calls == ['work-finished', 'runtime-stopped']
    assert bootstrap.get_supervisor() is None


def test_idle_exit_gateway_cannot_own_another_process_runtime(monkeypatch):
    from types import SimpleNamespace
    from gateway.run_local_runtime import start_owned_local_runtime
    from hermes_cli.local_runtime import ownership

    calls = []
    monkeypatch.setattr(ownership, 'start_gateway_owner', lambda: calls.append('owned'))
    runner = SimpleNamespace()
    start_owned_local_runtime(runner, idle_exit=True)
    assert not hasattr(runner, '_local_runtime_owner')
    assert calls == []


def test_named_gateway_does_not_inherit_root_boot_authority():
    from hermes_cli.local_runtime.ownership import start_gateway_owner
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(get_hermes_home() / 'profiles' / 'named')
    try:
        assert start_gateway_owner() is None
    finally:
        reset_hermes_home_override(token)
