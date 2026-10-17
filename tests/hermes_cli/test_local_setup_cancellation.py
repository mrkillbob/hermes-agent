"""Stop permanently cancels setup without releasing active download ownership early."""

import threading
from types import SimpleNamespace

import pytest

from tests.hermes_cli import test_local_quickstart as fixtures
from tests.waiting import wait_until

client = fixtures.client
quickstart_ready = fixtures.quickstart_ready


@pytest.fixture
def setup_plan(client, quickstart_ready, monkeypatch, tmp_path):
    from hermes_cli.web_routers import local_models as lm

    monkeypatch.setattr(lm, '_JOBS', {})
    monkeypatch.setattr(lm, '_RUNNING', {})
    monkeypatch.setattr(lm, '_QUICKSTART_LOCK', threading.Lock())
    entry = SimpleNamespace(id='fixture', display_name='Fixture')
    variant = SimpleNamespace(model_id='fixture-model')
    monkeypatch.setattr(lm, '_quickstart_target', lambda *a: (entry, variant))
    monkeypatch.setattr(lm.hardware, 'probe_budget', lambda **k: None)
    monkeypatch.setattr(lm, '_runtime_target', lambda: ('b1', 'cpu'))
    monkeypatch.setattr(lm, '_download_plan', lambda *a: [(None, tmp_path / 'model.gguf', 1)])
    monkeypatch.setattr(lm, '_terminate_state_pid', lambda: None)
    monkeypatch.setattr(lm, '_set_runtime_enabled', lambda value: None)
    monkeypatch.setattr(lm, '_ensure_server', lambda *a, **k: pytest.fail('cancelled setup booted'))
    return lm


def test_stop_terminalizes_paused_setup_and_releases_single_flight(client, setup_plan, monkeypatch):
    lm = setup_plan
    def paused(*a):
        raise lm.DownloadPaused('paused')
    monkeypatch.setattr(lm, '_run_download_plan', paused)
    response = client.post('/api/local-models/quickstart', json={})
    assert response.status_code == 200, response.text
    job_id = response.json()['job_id']
    wait_until(lambda: lm._JOBS[job_id]['status'] == 'paused', timeout=5)
    assert client.post('/api/local-models/server', json={'action': 'stop'}).status_code == 200
    job = client.get('/api/local-models/jobs/' + job_id).json()
    assert job['status'] == 'error' and 'Stop' in job['error']
    assert not job['can_resume'] and job_id not in lm._RUNNING
    assert not client.post('/api/local-models/download/resume', json={'job_id': job_id}).json()['resumed']
    assert client.post('/api/local-models/server', json={'action': 'stop'}).status_code == 200
    assert lm._QUICKSTART_LOCK.acquire(blocking=False)
    lm._QUICKSTART_LOCK.release()


@pytest.mark.platforms("any")
def test_stop_requests_active_transfer_cancel_but_waits_for_writer_cleanup(client, setup_plan, monkeypatch):
    lm = setup_plan
    entered, quiescing, release = threading.Event(), threading.Event(), threading.Event()
    def download(job, *a):
        entered.set()
        assert lm._RUNNING[job['job_id']]['pause'].wait(5)
        quiescing.set()
        assert release.wait(5)
        raise lm.DownloadPaused('paused')
    monkeypatch.setattr(lm, '_run_download_plan', download)
    response = client.post('/api/local-models/quickstart', json={})
    assert response.status_code == 200, response.text
    job_id = response.json()['job_id']
    try:
        assert entered.wait(5)
        assert client.post('/api/local-models/server', json={'action': 'stop'}).status_code == 200
        assert quiescing.wait(5), 'Stop did not signal the existing PM cancellation event'
        assert not lm._QUICKSTART_LOCK.acquire(blocking=False), 'overlapping writer admitted'
        assert lm._JOBS[job_id]['status'] == 'running'
    finally:
        release.set()
    wait_until(lambda: lm._JOBS[job_id]['status'] == 'error', timeout=5)
    wait_until(lambda: not lm._QUICKSTART_LOCK.locked(), timeout=5)
    assert job_id not in lm._RUNNING


def test_desktop_direct_quickstart_retains_eligible_root_before_planning(client, setup_plan, monkeypatch):
    from gateway.run_local_runtime import start_owned_local_runtime
    from hermes_cli import web_server
    from hermes_cli.local_runtime import ownership

    lm = setup_plan
    monkeypatch.setattr(ownership, '_OWNER', None)
    runner = SimpleNamespace(_running=True, _draining=False, session_runtime_descriptor={'state': 'ready'})
    start_owned_local_runtime(runner, idle_exit=True)
    monkeypatch.setattr(web_server.app.state, 'gateway_runner', runner, raising=False)
    accepted = []
    monkeypatch.setattr(lm, '_spawn_job', lambda job, *a, **k: accepted.append(job))
    try:
        response = client.post('/api/local-models/quickstart', json={})
        assert response.status_code == 200, response.text
        assert runner._idle_exit_promoted and runner._local_runtime_owner is ownership.current_owner()
        assert len(accepted) == 1
    finally:
        if lm._QUICKSTART_LOCK.locked():
            lm._QUICKSTART_LOCK.release()


def test_stop_during_preflight_cannot_register_a_stale_setup(client, setup_plan, monkeypatch):
    lm = setup_plan
    original = lm._quickstart_target
    def target(*a):
        lm._stop_server()
        return original(*a)
    monkeypatch.setattr(lm, '_quickstart_target', target)
    monkeypatch.setattr(lm, '_spawn_job', lambda *a, **k: pytest.fail('stale job registered after Stop'))
    response = client.post('/api/local-models/quickstart', json={})
    assert response.status_code == 409, response.text
    assert lm._JOBS == {} and not lm._QUICKSTART_LOCK.locked()


@pytest.mark.parametrize('iteration', range(10))
def test_cancel_resume_race_releases_once(setup_plan, iteration):
    lm = setup_plan
    job = lm._job('quickstart', 'race-' + str(iteration))
    released = []
    def body():
        raise lm.DownloadPaused('paused')
    lm._spawn_job(job, 'setup-race', body, resumable=True,
                  on_exit=lambda: released.append(True))
    wait_until(lambda: job['status'] == 'paused', timeout=5)
    resume, cancel = (lm._RUNNING[job['job_id']][key] for key in ('resume', 'cancel'))
    barrier = threading.Barrier(3)
    def race(action):
        barrier.wait(timeout=5)
        action()
    racers = [threading.Thread(target=race, args=(action,)) for action in (resume, cancel)]
    for racer in racers:
        racer.start()
    barrier.wait(timeout=5)
    for racer in racers:
        racer.join(5)
        assert not racer.is_alive()
    wait_until(lambda: job['status'] == 'error' and len(released) == 1, timeout=5)
    cancel()
    assert not resume() and released == [True] and job['job_id'] not in lm._RUNNING


def test_start_failure_and_late_cancellation_release_once(setup_plan, monkeypatch):
    lm = setup_plan
    job = lm._job('quickstart', 'start-failure')
    late, released = [], []
    class FailedThread:
        def start(self):
            late.append(lm._RUNNING[job['job_id']]['cancel'])
            raise RuntimeError('thread start failed')
    monkeypatch.setattr(lm, 'spawn_context_thread', lambda *a, **k: FailedThread())
    with pytest.raises(RuntimeError, match='thread start failed'):
        lm._spawn_job(job, 'setup-failure', lambda: pytest.fail('failed thread ran'),
                      resumable=True, on_exit=lambda: released.append(True))
    assert job['status'] == 'error' and job['error'] == 'thread start failed'
    late[0]()
    assert released == [True] and job['job_id'] not in lm._RUNNING


@pytest.mark.asyncio
async def test_activation_registration_does_not_block_stop_dispatch(client, setup_plan, monkeypatch):
    import asyncio
    from starlette.requests import Request

    lm = setup_plan
    monkeypatch.setattr(lm.bootstrap, 'staged_model_ids', lambda: {'fixture'})
    monkeypatch.setattr(lm, '_spawn_job', lambda *a, **k: None)
    held, loop_serviced = threading.Event(), threading.Event()
    starved = []
    def owner_boot():
        with lm.bootstrap._TRANSITION_LOCK:
            held.set()
            starved.append(not loop_serviced.wait(2))
    boot = threading.Thread(target=owner_boot)
    boot.start()
    assert held.wait(5)
    async def dispatch_other_request():
        loop_serviced.set()
    try:
        activation = asyncio.create_task(lm.local_models_activate(
            lm.ModelActivateBody(model_id='fixture'), Request({'type': 'http', 'app': client.app})))
        heartbeat = asyncio.create_task(dispatch_other_request())
        await asyncio.wait_for(asyncio.gather(activation, heartbeat), 5)
    finally:
        loop_serviced.set()
        boot.join(5)
    assert not boot.is_alive() and starved == [False], 'Activate prevented the loop dispatching Stop'


@pytest.mark.parametrize('action', ['start', 'activate'])
def test_explicit_root_controls_also_retain_ready_idle_gateway(client, setup_plan, monkeypatch, action):
    from gateway.run_local_runtime import start_owned_local_runtime
    from hermes_cli import web_server
    from hermes_cli.local_runtime import ownership

    lm = setup_plan
    monkeypatch.setattr(ownership, '_OWNER', None)
    runner = SimpleNamespace(_running=True, _draining=False, session_runtime_descriptor={'state': 'ready'})
    start_owned_local_runtime(runner, idle_exit=True)
    monkeypatch.setattr(web_server.app.state, 'gateway_runner', runner, raising=False)
    monkeypatch.setattr(lm.bootstrap, 'staged_model_ids', lambda: {'fixture'})
    monkeypatch.setattr(lm, '_spawn_job', lambda *a, **k: None)
    monkeypatch.setattr(lm, '_start_local_server', lambda *a: None)
    if action == 'start':
        response = client.post('/api/local-models/server', json={'action': action})
    else:
        response = client.post('/api/local-models/activate', json={'model_id': 'fixture'})
    assert response.status_code == 200, response.text
    assert runner._idle_exit_promoted and runner._local_runtime_owner is ownership.current_owner()


def test_stop_does_not_retain_a_consumer_gateway(client, setup_plan, monkeypatch):
    from gateway.run_local_runtime import start_owned_local_runtime
    from hermes_cli import web_server
    from hermes_cli.local_runtime import ownership

    monkeypatch.setattr(ownership, '_OWNER', None)
    runner = SimpleNamespace(_running=True, _draining=False, session_runtime_descriptor={'state': 'ready'})
    start_owned_local_runtime(runner, idle_exit=True)
    monkeypatch.setattr(web_server.app.state, 'gateway_runner', runner, raising=False)
    response = client.post('/api/local-models/server', json={'action': 'stop'})
    assert response.status_code == 409, response.text
    assert not runner._idle_exit_promoted and ownership.current_owner() is None
