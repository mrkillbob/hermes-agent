"""Consumers must not tie a shared machine router to their own process lifetime."""

from types import SimpleNamespace

import pytest


@pytest.fixture
def inert_runtime(tmp_path, monkeypatch):
    from hermes_cli.local_runtime import binaries, bootstrap, endpoint, supervisor

    calls = []
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(bootstrap, '_SERVING_ENGINE', None)
    monkeypatch.setattr(bootstrap, '_EXIT_HOOKED', True)
    monkeypatch.setattr(bootstrap, 'adopt_legacy_models', list)
    monkeypatch.setattr(bootstrap, 'staged_models', lambda: [tmp_path / 'fixture.gguf'])
    monkeypatch.setattr(bootstrap, '_admitted_models_max', lambda directory, configured: configured)
    monkeypatch.setattr(bootstrap, '_generate_presets', lambda *args: None)
    monkeypatch.setattr(bootstrap, '_start_idle_sweeper', lambda sup: None)
    monkeypatch.setattr(endpoint, '_state_endpoint', lambda: None)
    monkeypatch.setattr(binaries, 'installed_engine', lambda backend: SimpleNamespace(
        binary=tmp_path / 'fixture-server', backend=backend, tag='fixture'))

    class InertSupervisor:
        base_url = 'http://127.0.0.1:1/v1'

        def __init__(self, *args, **kwargs):
            calls.append('constructed')

        def start(self, **kwargs):
            calls.append('started')

        def stop(self):
            calls.append('stopped')

    monkeypatch.setattr(supervisor, 'LlamaServerSupervisor', InertSupervisor)
    return bootstrap, calls


@pytest.mark.parametrize('force', [False, True])
def test_consumer_cannot_spawn_or_own_shared_runtime(inert_runtime, force):
    bootstrap, calls = inert_runtime
    config = {'local_runtime': {'enabled': True, 'backend': 'cpu', 'models_max': 1}}
    assert bootstrap.ensure_local_runtime(config, force=force) is None
    assert bootstrap.get_supervisor() is None
    bootstrap.shutdown_local_runtime()
    assert calls == []


@pytest.fixture
def owner():
    from hermes_cli.local_runtime.ownership import RuntimeOwner
    from hermes_constants import get_hermes_home

    return RuntimeOwner(get_hermes_home())


@pytest.mark.parametrize('enabled', [False, None])
def test_durable_owner_preserves_profile_opt_out(inert_runtime, owner, enabled):
    bootstrap, calls = inert_runtime
    section = {} if enabled is None else {'enabled': enabled}
    assert bootstrap.ensure_local_runtime({'local_runtime': section}, owner=owner) is None
    assert calls == []


@pytest.mark.parametrize('failure', ['unwritable', 'contended'])
def test_lock_failure_refuses_spawn_and_model_writes(inert_runtime, owner, monkeypatch, tmp_path, failure):
    bootstrap, calls = inert_runtime
    monkeypatch.setattr(bootstrap, 'adopt_legacy_models', lambda: calls.append('moved-models'))
    if failure == 'unwritable':
        blocker = tmp_path / 'not-a-directory'
        blocker.write_text('fixture')
        monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: blocker / 'runtime')
    else:
        lock = bootstrap._cross_process_boot_lock
        monkeypatch.setattr(bootstrap, '_try_lock_boot_fd', lambda fd: False)
        monkeypatch.setattr(bootstrap, '_cross_process_boot_lock', lambda **kwargs: lock(timeout_s=0, **kwargs))
    assert bootstrap.ensure_local_runtime({'local_runtime': {'enabled': True}}, owner=owner) is None
    assert calls == []


@pytest.mark.parametrize('stale', [False, True])
def test_foreign_live_owner_is_never_replaced(inert_runtime, owner, monkeypatch, stale):
    from hermes_cli.local_runtime import recovery

    bootstrap, calls = inert_runtime
    monkeypatch.setattr(recovery, 'read_state', lambda: {'owner_pid': -1, 'create_time': 1})
    monkeypatch.setattr(recovery, '_owner_is_dead', lambda state: False)
    monkeypatch.setattr(bootstrap, '_presets_stale', lambda: stale)
    monkeypatch.setattr(recovery, 'stop_recorded_orphan', lambda: calls.append('stopped-foreign-owner'))
    monkeypatch.setattr(bootstrap, 'adopt_legacy_models', lambda: calls.append('moved-models'))
    assert bootstrap.ensure_local_runtime({'local_runtime': {'enabled': True}}, owner=owner) is None
    assert calls == []


def test_unreadable_incumbent_record_refuses_replacement(inert_runtime, owner):
    from hermes_cli.local_runtime.supervisor import state_path

    bootstrap, calls = inert_runtime
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text('{broken record')
    assert bootstrap.ensure_local_runtime({'local_runtime': {'enabled': True}}, owner=owner) is None
    assert calls == []


@pytest.mark.asyncio
async def test_shutdown_cancels_boot_before_supervisor_publication(inert_runtime, owner, monkeypatch):
    import asyncio
    import threading
    from gateway.run_local_runtime import stop_owned_local_runtime
    from hermes_cli.local_runtime import supervisor

    bootstrap, calls = inert_runtime
    entered = threading.Event()
    stopped = threading.Event()
    results = []

    class PendingSupervisor:
        def __init__(self, *args, **kwargs):
            pass

        def start(self, *, cancel_event):
            entered.set()
            assert cancel_event.wait(5), 'shutdown never cancelled the pending boot'
            raise RuntimeError('fixture boot cancelled')

        def stop(self):
            stopped.set()

    monkeypatch.setattr(supervisor, 'LlamaServerSupervisor', PendingSupervisor)
    worker = threading.Thread(target=lambda: results.append(bootstrap.ensure_local_runtime(
        {'local_runtime': {'enabled': True}}, owner=owner)))
    worker.start()
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert bootstrap.get_supervisor() is None
        owner.close()
        await asyncio.wait_for(stop_owned_local_runtime(5), 10)
        assert stopped.is_set()
        assert bootstrap.get_supervisor() is None
        assert bootstrap.ensure_local_runtime({'local_runtime': {'enabled': True}}, force=True, owner=owner) is None
    finally:
        owner.close()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert results == [None]
    assert calls == []


def test_closed_owner_does_not_run_a_queued_boot(inert_runtime, owner, monkeypatch):
    from agent import memory_provider

    bootstrap, calls = inert_runtime
    queued = []

    class DeferredThread:
        def start(self):
            pass

    monkeypatch.setattr(memory_provider, 'spawn_context_thread',
                        lambda target, **kwargs: queued.append(target) or DeferredThread())
    owner.request_boot()
    owner.close()
    queued.pop()()
    assert bootstrap.get_supervisor() is None
    assert calls == []


def test_scoped_request_boot_uses_only_owner_policy(inert_runtime, owner, monkeypatch):
    from hermes_cli.local_runtime import binaries, endpoint, ownership
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
    from tests.waiting import wait_until

    bootstrap, calls = inert_runtime
    owner.home.mkdir(parents=True, exist_ok=True)
    config_path = owner.home / 'config.yaml'
    original = 'model:\n  provider: cloud-fixture\nlocal_runtime:\n  enabled: true\n  backend: metal\n  models_max: 1\n'
    config_path.write_text(original)
    monkeypatch.setattr(ownership, '_OWNER', owner)
    backends = []
    monkeypatch.setattr(binaries, 'installed_engine', lambda backend: backends.append(backend) or SimpleNamespace(
        binary=owner.home / 'fixture', backend=backend, tag='fixture'))
    named_home = owner.home / 'profiles' / 'named'
    token = set_hermes_home_override(named_home)
    try:
        endpoint._kick_managed_boot({'local_runtime': {'enabled': True, 'backend': 'cpu', 'models_max': 4}})
        assert wait_until(lambda: bootstrap.get_supervisor() is not None, timeout=5)
        assert get_hermes_home() == named_home
        assert ownership.current_owner(for_scope=True) is None
    finally:
        reset_hermes_home_override(token)
    assert backends == ['metal']
    assert calls == ['constructed', 'started']
    assert config_path.read_text() == original


def test_foreign_refresh_cannot_acquire_ownership(inert_runtime):
    bootstrap, calls = inert_runtime
    assert bootstrap.refresh_local_runtime() is False
    assert calls == []


def test_shared_busy_model_defers_owner_refresh(inert_runtime, owner, monkeypatch):
    bootstrap, calls = inert_runtime
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', SimpleNamespace(is_idle=lambda: False))
    monkeypatch.setattr(bootstrap, '_SERVING_OWNER', owner)
    assert bootstrap.refresh_local_runtime() is False
    assert calls == []


def test_owner_environment_expansion_does_not_borrow_a_routed_scope(inert_runtime, monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from hermes_cli.local_runtime import binaries, ownership
    from hermes_constants import get_hermes_home

    _bootstrap, calls = inert_runtime
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text('local_runtime:\n  enabled: true\n  backend: ${FIXTURE_ENGINE}\n')
    scope = set_secret_scope({'FIXTURE_ENGINE': 'metal'}, profile_home=str(home))
    try:
        owner = ownership.RuntimeOwner(home)
    finally:
        reset_secret_scope(scope)
    backends = []
    monkeypatch.setattr(binaries, 'installed_engine', lambda backend: backends.append(backend) or SimpleNamespace(
        binary=home / 'fixture', backend=backend, tag='fixture'))
    routed = set_secret_scope({'FIXTURE_ENGINE': 'cpu'}, profile_home=str(home / 'profiles' / 'named'))
    try:
        assert owner.ensure() is not None
    finally:
        reset_secret_scope(routed)
    assert backends == ['metal']
    assert calls == ['constructed', 'started']


@pytest.mark.asyncio
async def test_owner_close_cancels_boot_lock_wait(inert_runtime, owner, monkeypatch):
    import asyncio
    import threading
    from gateway.run_local_runtime import stop_owned_local_runtime

    bootstrap, calls = inert_runtime
    entered = threading.Event()

    def contended(fd):
        entered.set()
        return False

    monkeypatch.setattr(bootstrap, '_try_lock_boot_fd', contended)
    worker = threading.Thread(target=lambda: bootstrap.ensure_local_runtime(
        {'local_runtime': {'enabled': True}}, owner=owner))
    worker.start()
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        owner.close()
        await asyncio.wait_for(stop_owned_local_runtime(5), 10)
    finally:
        owner.close()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert calls == []
