"""Crash, admission and retention contracts using inert local coordinator state."""
import argparse
import hashlib
import json
from types import SimpleNamespace

import pytest

from hermes_cli.fleet_runner import FleetRunner
from tests.hermes_cli.test_fleet_execution_lifecycle import _setup


@pytest.fixture(autouse=True)
def profile_env(tmp_path, monkeypatch):
    from pathlib import Path
    root = tmp_path / '.hermes'
    target = root / 'profiles' / 'worker'
    target.mkdir(parents=True)
    (target / 'config.yaml').write_text('model: fixture\n')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))


def _runner(store, cap, tmp_path, **kwargs):
    return FleetRunner('mac', store, 'hermes', ['worker'], [], capabilities=[cap],
                       state_dir=tmp_path / 'receipts', **kwargs)


def _capture(tmp_path, claim):
    directory = tmp_path / 'receipts' / hashlib.sha256(claim.claim_id.encode()).hexdigest()
    directory.mkdir(parents=True)
    (directory / 'claim.json').write_text(json.dumps(dict(task_id=claim.task_id,
        claim_id=claim.claim_id, node_id='mac', profile='worker')))
    (directory / 'stdout-proof').write_text(json.dumps(dict(type='system', subtype='admission',
        session_id='session', admission_id='admission')))
    (directory / 'stderr-proof').write_text('retained diagnostic')
    return directory


def test_cli_runner_uses_stable_profile_and_node_scoped_state(tmp_path, monkeypatch):
    from hermes_cli.subcommands import fleet
    import hermes_cli.fleet_runner as module
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    launcher = tmp_path / 'hermes'
    launcher.touch()
    live = tmp_path / 'live'
    live.touch()
    monkeypatch.setattr(fleet, '_config', dict)
    monkeypatch.setattr(fleet, '_client', lambda _: object())
    observed = []

    class Runner:
        def __init__(self, *args, **kwargs):
            observed.append(kwargs.get('state_dir'))
        def run_once(self):
            return True

    monkeypatch.setattr(module, 'FleetRunner', Runner)
    args = argparse.Namespace(fleet_action='runner', hermes_executable=str(launcher),
        liveness_file=str(live), node_id='node/unsafe', profile=['worker'], model=[], tool=[],
        project=[], once=True)
    assert fleet.cmd_fleet(args) == 0
    assert fleet.cmd_fleet(args) == 0
    assert observed[0] is not None
    assert observed[0] == observed[1]
    assert observed[0].is_relative_to(tmp_path / 'home')


def test_terminal_failure_before_admission_is_failed_not_uncertain(tmp_path):
    store, cap = _setup(tmp_path)
    runner = _runner(store, cap, tmp_path, executor=lambda *_: SimpleNamespace(returncode=1,
        stdout=json.dumps(dict(type='result', admission_attempted=False, exit_code=1,
                               error='Invalid model configuration', text='')),
        stderr=''))
    runner.run_once()
    assert store.list_tasks()[0].status == 'failed'
    assert 'Invalid model configuration' in store.list_tasks()[0].error


@pytest.mark.parametrize('settled', [False, True])
def test_expired_recovery_reads_admission_without_renewing_or_cancelling(tmp_path, monkeypatch, settled):
    store, cap = _setup(tmp_path)
    claim = store.claim_task(cap, now=100, lease_seconds=1)
    store.reclaim_expired(now=102)
    directory = _capture(tmp_path, claim)
    seen = []

    def probe(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(type='result', settled=settled,
            admission_status='completed' if settled else 'started', exit_code=0, text='recovered')), stderr='')

    monkeypatch.setattr('hermes_cli._subprocess_compat.bounded_probe_run', probe)
    monkeypatch.setattr(store, 'renew_claim', lambda *_a, **_k: pytest.fail('Recovery must not renew expired claim'))
    runner = _runner(store, cap, tmp_path, executor=lambda *_: pytest.fail('Must never replay'))
    runner.run_once()
    assert len(seen) == 1
    assert '--cancel' not in seen[0]
    assert store.list_tasks()[0].status == ('completed' if settled else 'uncertain')
    assert directory.exists() is (not settled)


def test_claim_receipt_survives_task_lookup_failure(tmp_path, monkeypatch):
    store, cap = _setup(tmp_path)
    runner = _runner(store, cap, tmp_path, executor=lambda *_: pytest.fail('Must not execute'))
    monkeypatch.setattr(runner, '_find_task', lambda _: (_ for _ in ()).throw(OSError('lookup unavailable')))
    with pytest.raises(OSError, match='lookup unavailable'):
        runner.run_once()
    journals = list((tmp_path / 'receipts').glob('*/claim.json'))
    assert len(journals) == 1
    saved = json.loads(journals[0].read_text())
    assert saved['claim_id'] == store.list_tasks()[0].claim_id
    restarted = _runner(store, cap, tmp_path, executor=lambda *_: pytest.fail('Must never replay'))
    restarted.run_once()
    assert store.list_tasks()[0].status == 'failed'


def test_acknowledged_recovery_removes_captures_but_failed_ack_retains_them(tmp_path, monkeypatch):
    store, cap = _setup(tmp_path)
    claim = store.claim_task(cap)
    directory = _capture(tmp_path, claim)
    monkeypatch.setattr(FleetRunner, '_reconcile', lambda *_a, **_k: SimpleNamespace(returncode=0,
        stdout=json.dumps(dict(type='result', settled=True, text='done', exit_code=0)), stderr=''))
    complete = store.complete_task
    monkeypatch.setattr(store, 'complete_task', lambda *_a, **_k: False)
    _runner(store, cap, tmp_path).run_once()
    assert (directory / 'stdout-proof').is_file()
    assert (directory / 'stderr-proof').is_file()
    monkeypatch.setattr(store, 'complete_task', complete)
    _runner(store, cap, tmp_path).run_once()
    assert not directory.exists()


@pytest.mark.parametrize('path', ['execute', 'reconcile'])
def test_child_uses_target_profile_environment(tmp_path, monkeypatch, path):
    from pathlib import Path
    root = tmp_path / '.hermes'
    target = root / 'profiles' / 'worker'
    target.mkdir(parents=True, exist_ok=True)
    (root / '.env').write_text('HERMES_MODEL=launch-model\nTELEGRAM_BOT_TOKEN=launch-token\n')
    (target / '.env').write_text('HERMES_MODEL=target-model\nTELEGRAM_BOT_TOKEN=target-token\n')
    (target / 'config.yaml').write_text('model: target-model\n')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.setenv('HERMES_MODEL', 'launch-model')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'launch-token')
    monkeypatch.setenv('TERMINAL_CWD', '/launch-private')
    # The routing home is cached at boot; bind only its documented test seam.
    monkeypatch.setattr('hermes_constants.get_routing_process_hermes_home', lambda: root)
    seen = []

    def probe(argv, **kwargs):
        seen.append(kwargs.get('env'))
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(type='result', settled=True, text='ok')), stderr='')

    runner = FleetRunner('mac', None, 'hermes', ['worker'], [])
    if path == 'execute':
        monkeypatch.setattr(FleetRunner, '_execute', staticmethod(lambda argv, body, **kwargs: probe(argv, **kwargs)))
        runner._execute_task(SimpleNamespace(body='inert', requirement=SimpleNamespace(models=())), 'worker')
    else:
        monkeypatch.setattr('hermes_cli._subprocess_compat.bounded_probe_run', probe)
        runner._reconcile('worker', dict(session_id='session', admission_id='admission'), cancel=False,
                          original=SimpleNamespace(stdout='', stderr=''))
    assert seen[0]['HERMES_HOME'] == str(target)
    assert seen[0]['TELEGRAM_BOT_TOKEN'] == 'target-token'
    assert seen[0].get('HERMES_MODEL') != 'launch-model'
    assert 'TERMINAL_CWD' not in seen[0]


@pytest.mark.parametrize('submit_attempted', [False, True])
def test_cli_failure_marker_distinguishes_lost_submit_ack(tmp_path, capsys, submit_attempted):
    import asyncio
    from hermes_cli.gateway_chat_view import GatewayChatView
    from hermes_cli.stream_json import StreamJsonEmitter

    class Peer:
        async def rpc(self, method, **kwargs):
            assert method == 'prompt.submit'
            raise OSError('response lost after acceptance')

    emitter = StreamJsonEmitter(session_id='session')
    if submit_attempted:
        view = GatewayChatView(Peer(), {'stored_session_id': 'session'}, emitter=emitter)
        with pytest.raises(OSError, match='response lost'):
            asyncio.run(view.submit('inert task'))
    emitter.emit_result({'failed': True, 'error': 'Gateway connection failed'}, exit_code=1)
    output = capsys.readouterr().out
    store, cap = _setup(tmp_path)
    runner = _runner(store, cap, tmp_path, executor=lambda *_: SimpleNamespace(
        returncode=1, stdout=output, stderr=''))
    runner.run_once()
    assert store.list_tasks()[0].status == ('uncertain' if submit_attempted else 'failed')


def test_legacy_terminal_failure_without_admission_evidence_stays_uncertain(tmp_path):
    store, cap = _setup(tmp_path)
    runner = _runner(store, cap, tmp_path, executor=lambda *_: SimpleNamespace(returncode=1,
        stdout=json.dumps(dict(type='result', exit_code=1, error='Gateway read failed')), stderr=''))
    runner.run_once()
    assert store.list_tasks()[0].status == 'uncertain'


def test_idle_coordinator_reclaims_without_another_runner_request(tmp_path, monkeypatch):
    import threading
    from hermes_cli.fleet_server import FleetServer
    store, cap = _setup(tmp_path)
    store.claim_task(cap, now=100, lease_seconds=1)
    monkeypatch.setattr('hermes_cli.fleet_store._now', lambda: 102)
    swept = threading.Event()
    original = store.reclaim_expired

    def reclaim(**kwargs):
        count = original(**kwargs)
        if count:
            swept.set()
        return count

    monkeypatch.setattr(store, 'reclaim_expired', reclaim)
    updates = []
    mirrored = threading.Event()

    def mirror(task_id, status, **fields):
        # Mirroring must happen after the SQLite expiry transition commits.
        assert store.list_tasks()[0].status == 'uncertain'
        updates.append((task_id, status, fields))
        mirrored.set()

    server = FleetServer(store, token='inert-local-token', on_task_update=mirror)
    try:
        server.start()
        assert swept.wait(5), 'Idle coordinator never swept expired claims'
        assert mirrored.wait(5), 'Expiry never notified the existing Kanban consumer'
        assert updates == [('task', 'uncertain', {'error': 'Claim expired; reconcile gateway outcome before retry'})]
        assert store.list_tasks()[0].status == 'uncertain'
        assert store.list_runners(now=102)[0]['active_load'] == 0
    finally:
        server.close()


def test_missing_recovery_profile_holds_claim_without_launch(tmp_path, monkeypatch):
    store, cap = _setup(tmp_path)
    claim = store.claim_task(cap)
    directory = _capture(tmp_path, claim)
    (tmp_path / '.hermes' / 'profiles' / 'worker' / 'config.yaml').unlink()
    monkeypatch.setattr('hermes_cli._subprocess_compat.bounded_probe_run',
                        lambda *_a, **_k: pytest.fail('Missing profile must not launch'))
    runner = _runner(store, cap, tmp_path, executor=lambda *_: pytest.fail('Must never replay'))
    assert not runner.run_once()
    assert store.list_tasks()[0].status == 'uncertain'
    assert directory.is_dir()
