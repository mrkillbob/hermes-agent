"""Quickstart must not download weights an already-running router cannot register."""

from types import SimpleNamespace

import pytest

from tests.hermes_cli import test_local_setup_cancellation as fixtures

client = fixtures.client
quickstart_ready = fixtures.quickstart_ready
setup_plan = fixtures.setup_plan


@pytest.mark.parametrize('kind', ['owned', 'adopted'])
def test_unregistered_model_refused_before_job_or_download(client, setup_plan, monkeypatch, kind):
    lm = setup_plan
    observed, accepted = [], []
    class Server:
        def models(self, timeout_s=30):
            observed.append(('models', timeout_s))
            return {'other-model': 'loaded'}
    monkeypatch.setattr(lm.bootstrap, 'get_supervisor', lambda: Server() if kind == 'owned' else None)
    monkeypatch.setattr(lm, '_state_endpoint', lambda: {'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'fixture'})
    def listing(endpoint, path, *, timeout):
        observed.append((path, timeout))
        return {'data': [{'id': 'other-model'}]}
    monkeypatch.setattr(lm, '_router_request', listing)
    monkeypatch.setattr(lm, '_spawn_job', lambda *a, **k: accepted.append(True))
    monkeypatch.setattr(lm.bootstrap, 'refresh_local_runtime', lambda: pytest.fail('shared router refreshed'))
    monkeypatch.setattr(lm, '_run_download_plan', lambda *a: pytest.fail('unusable model downloaded'))
    response = client.post('/api/local-models/quickstart', json={})
    assert response.status_code == 409, response.text
    assert 'Stop the local server' in response.json()['detail']
    assert observed and accepted == [] and lm._JOBS == {} and not lm._QUICKSTART_LOCK.locked()


@pytest.mark.parametrize('kind', ['owned', 'adopted'])
def test_registered_model_can_be_setup_without_reloading_shared_server(client, setup_plan, monkeypatch, kind):
    lm = setup_plan
    observed, accepted = [], []
    class Server:
        def models(self, timeout_s=30):
            observed.append(timeout_s)
            return {'fixture-model': 'loaded'}
    monkeypatch.setattr(lm.bootstrap, 'get_supervisor', lambda: Server() if kind == 'owned' else None)
    monkeypatch.setattr(lm, '_state_endpoint', lambda: {'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'fixture'})
    def listing(endpoint, path, *, timeout):
        assert path == '/models'
        observed.append(timeout)
        return {'data': [{'id': 'fixture-model'}]}
    monkeypatch.setattr(lm, '_router_request', listing)
    monkeypatch.setattr(lm, '_spawn_job', lambda *a, **k: accepted.append(True))
    monkeypatch.setattr(lm.bootstrap, 'refresh_local_runtime', lambda: pytest.fail('shared router refreshed'))
    try:
        response = client.post('/api/local-models/quickstart', json={})
        assert response.status_code == 200, response.text
        assert observed == [5] and accepted == [True]
    finally:
        if lm._QUICKSTART_LOCK.locked():
            lm._QUICKSTART_LOCK.release()


def test_cold_setup_does_not_query_an_absent_server(client, setup_plan, monkeypatch):
    lm = setup_plan
    monkeypatch.setattr(lm.bootstrap, 'get_supervisor', lambda: None)
    monkeypatch.setattr(lm, '_state_endpoint', lambda: None)
    monkeypatch.setattr(lm, '_router_request', lambda *a, **k: pytest.fail('queried absent server'))
    accepted = []
    monkeypatch.setattr(lm, '_spawn_job', lambda *a, **k: accepted.append(True))
    try:
        response = client.post('/api/local-models/quickstart', json={})
        assert response.status_code == 200, response.text
        assert accepted == [True]
    finally:
        if lm._QUICKSTART_LOCK.locked():
            lm._QUICKSTART_LOCK.release()


@pytest.mark.parametrize('uncertain', ['booting', 'listing-unavailable'])
def test_uncertain_running_server_refuses_before_job_creation(client, setup_plan, monkeypatch, uncertain):
    from hermes_cli.local_runtime import boot_status

    lm = setup_plan
    monkeypatch.setattr(boot_status, 'owner_boot_pending', lambda: uncertain == 'booting')
    monkeypatch.setattr(lm.bootstrap, 'get_supervisor', lambda: None)
    monkeypatch.setattr(lm, '_state_endpoint', lambda: {'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'fixture'})
    def unavailable(*a, **k):
        raise OSError('fixture listing unavailable')
    monkeypatch.setattr(lm, '_router_request', unavailable)
    monkeypatch.setattr(lm, '_spawn_job', lambda *a, **k: pytest.fail('uncertain setup accepted'))
    response = client.post('/api/local-models/quickstart', json={})
    assert response.status_code == 409, response.text
    assert lm._JOBS == {} and not lm._QUICKSTART_LOCK.locked()


def test_repriced_model_is_checked_again_before_weight_transfer(client, setup_plan, monkeypatch, tmp_path):
    from tests.waiting import wait_until

    lm = setup_plan
    calls = []
    class Server:
        def models(self, timeout_s=30):
            return {'fixture-model': 'loaded'}
    monkeypatch.setattr(lm.bootstrap, 'get_supervisor', lambda: Server())
    monkeypatch.setattr(lm.binaries, 'installed_engine', lambda *a, **k: None)
    monkeypatch.setattr(lm, '_install_engine_job', lambda *a: calls.append('install'))
    monkeypatch.setattr(lm, '_repriced_quickstart', lambda *a: (
        SimpleNamespace(id='new', display_name='New'), SimpleNamespace(model_id='new-model'),
        [(None, tmp_path / 'new.gguf', 1)]))
    monkeypatch.setattr(lm, '_run_download_plan', lambda *a: calls.append('download'))
    monkeypatch.setattr(lm, '_set_runtime_enabled', lambda *a: calls.append('enabled'))
    monkeypatch.setattr(lm, '_assign_default', lambda *a: calls.append('assigned'))
    response = client.post('/api/local-models/quickstart', json={})
    assert response.status_code == 200, response.text
    job = lm._JOBS[response.json()['job_id']]
    assert wait_until(lambda: job['status'] == 'error', timeout=5)
    assert 'not registered' in job['error'] and calls == ['install']
    assert wait_until(lambda: not lm._QUICKSTART_LOCK.locked(), timeout=5)
