"""Runtime control and consumer regression contracts from source review."""

import threading

import pytest

from tests.hermes_cli import test_local_server_lifecycle as lifecycle_fixtures
from tests.hermes_cli import test_local_runtime_ownership as ownership_fixtures

client = lifecycle_fixtures.client
inert_runtime = ownership_fixtures.inert_runtime
owner = ownership_fixtures.owner


def test_stop_cancels_pending_owned_boot_before_publication(client, monkeypatch):
    from hermes_cli.local_runtime import bootstrap, ownership
    from hermes_cli.web_routers import local_models

    cancel = threading.Event()
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(bootstrap, '_BOOT_CANCEL', cancel)
    monkeypatch.setattr(bootstrap, '_BOOT_OWNER', ownership.current_owner())
    recovered, disabled = [], []
    monkeypatch.setattr(local_models, '_terminate_state_pid', lambda: recovered.append(cancel.is_set()))
    monkeypatch.setattr(local_models, '_set_runtime_enabled', lambda value: disabled.append(value))
    response = client.post('/api/local-models/server', json={'action': 'stop'})
    assert response.status_code == 200, response.text
    assert cancel.is_set() and recovered == [True] and disabled == [False]


@pytest.mark.parametrize('scope', ['named', 'standalone'])
def test_orphan_stop_requires_root_owner_scope(client, monkeypatch, scope):
    from hermes_cli.local_runtime import bootstrap, ownership
    from hermes_cli.web_routers import local_models
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(bootstrap, '_BOOT_CANCEL', None)
    actions = []
    monkeypatch.setattr(local_models, '_terminate_state_pid', lambda: actions.append('terminated'))
    monkeypatch.setattr(local_models, '_set_runtime_enabled', lambda value: actions.append('wrote'))
    token = None
    if scope == 'named':
        token = set_hermes_home_override(get_hermes_home() / 'profiles' / 'named')
    else:
        monkeypatch.setattr(ownership, '_OWNER', None)
    try:
        response = client.post('/api/local-models/server', json={'action': 'stop'})
        assert response.status_code == 409, response.text
        assert actions == []
    finally:
        if token is not None:
            reset_hermes_home_override(token)


def test_stop_fences_owner_with_captured_enabled_snapshot(client, inert_runtime, owner, monkeypatch):
    from hermes_cli import config
    from hermes_cli.web_routers import local_models

    bootstrap, calls = inert_runtime
    captured, release = threading.Event(), threading.Event()

    def stale_config():
        captured.set()
        assert release.wait(5)
        return {'local_runtime': {'enabled': True, 'backend': 'cpu'}}

    monkeypatch.setattr(config, 'load_config_readonly', stale_config)
    monkeypatch.setattr(local_models, '_terminate_state_pid', lambda: None)
    monkeypatch.setattr(local_models, '_set_runtime_enabled', lambda value: None)
    results = []
    worker = threading.Thread(target=lambda: results.append(owner.ensure()))
    worker.start()
    try:
        assert captured.wait(5)
        response = client.post('/api/local-models/server', json={'action': 'stop'})
        assert response.status_code == 200, response.text
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results == [None] and calls == [] and bootstrap.get_supervisor() is None


def test_ownerless_consumer_does_not_wait_for_unrequestable_boot(monkeypatch):
    from hermes_cli.local_runtime import detect, endpoint, ownership

    monkeypatch.setattr(endpoint, '_state_endpoint', lambda: None)
    monkeypatch.setattr(endpoint, '_boot_in_flight', lambda config: True)
    monkeypatch.setattr(ownership, '_OWNER', None)
    monkeypatch.setattr(detect, 'detect_server', lambda **kwargs: None)
    monkeypatch.setattr(endpoint.time, 'sleep', lambda seconds: pytest.fail('ownerless boot wait'))
    assert endpoint.resolve_llamacpp_endpoint(wait_for_boot_s=8) is None


def test_stop_fences_owner_snapshot_taken_during_disable_write(client, inert_runtime, owner, monkeypatch):
    from hermes_cli import config
    from hermes_cli.web_routers import local_models

    bootstrap, calls = inert_runtime
    captured, release = threading.Event(), threading.Event()
    results = []
    def stale_config():
        captured.set()
        assert release.wait(5)
        return {'local_runtime': {'enabled': True, 'backend': 'cpu'}}
    monkeypatch.setattr(config, 'load_config_readonly', stale_config)
    monkeypatch.setattr(local_models, '_terminate_state_pid', lambda: None)
    worker = threading.Thread(target=lambda: results.append(owner.ensure()))
    def persist_disabled(value):
        assert value is False
        worker.start()
        assert captured.wait(5)
    monkeypatch.setattr(local_models, '_set_runtime_enabled', persist_disabled)
    try:
        response = client.post('/api/local-models/server', json={'action': 'stop'})
        assert response.status_code == 200, response.text
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(5)
    assert not worker.is_alive()
    assert results == [None] and calls == [] and bootstrap.get_supervisor() is None


def test_stop_serializes_start_snapshot_and_allows_fresh_start(client, inert_runtime, monkeypatch):
    from hermes_cli.web_routers import local_models
    from tests.waiting import wait_until

    bootstrap, calls = inert_runtime
    captured, release, stop_done = threading.Event(), threading.Event(), threading.Event()
    enabled = {'value': False}
    def configure(value):
        enabled['value'] = value
        if value:
            captured.set()
            assert release.wait(5)
        return {'local_runtime': {'enabled': value, 'backend': 'cpu'}}
    monkeypatch.setattr(local_models, '_set_runtime_enabled', configure)
    monkeypatch.setattr(local_models, '_terminate_state_pid', lambda: None)
    monkeypatch.setattr(local_models, '_state_endpoint', lambda: None)
    starts, stops = [], []
    def stop():
        stops.append(client.post('/api/local-models/server', json={'action': 'stop'}))
        stop_done.set()
    starter = threading.Thread(target=lambda: starts.append(
        client.post('/api/local-models/server', json={'action': 'start'})))
    stopper = threading.Thread(target=stop)
    starter.start()
    try:
        assert captured.wait(5)
        stopper.start()
        assert wait_until(lambda: stop_done.is_set() or bootstrap._STOPPING > 0, timeout=5)
        assert not stop_done.is_set(), 'Stop completed before the older Start snapshot settled'
    finally:
        release.set()
        starter.join(5)
        if stopper.ident is not None:
            stopper.join(5)
    assert not starter.is_alive() and not stopper.is_alive()
    assert starts[0].status_code == 502 and stops[0].status_code == 200
    assert calls == [] and enabled['value'] is False and bootstrap.get_supervisor() is None
    restarted = client.post('/api/local-models/server', json={'action': 'start'})
    assert restarted.status_code == 200, restarted.text
    assert calls == ['constructed', 'started'] and enabled['value'] is True
