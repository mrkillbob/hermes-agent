"""Fleet execution remains leased until an authoritative outcome is acknowledged."""
import json
import threading
import pytest
from types import SimpleNamespace

from hermes_cli.fleet_protocol import FleetTask, RunnerCapability, TaskRequirement
from hermes_cli.fleet_runner import FleetRunner
from hermes_cli.fleet_store import FleetStore


class _Coordinator(FleetStore):
    def heartbeat_runner(self, capability, **kwargs):
        return super().heartbeat_runner(capability.node_id, capability.profile, **kwargs)


def _setup(tmp_path):
    store = _Coordinator(tmp_path / 'fleet.db')
    cap = RunnerCapability('mac', 'worker', tools=('terminal',), projects=('Hermes Agent',), platform='test')
    store.register_runner(cap)
    store.submit_task(FleetTask('task', 'Read', 'Read only', TaskRequirement(), 'task'))
    return store, cap


def test_expired_execution_is_held_until_original_claim_reconciles(tmp_path):
    store, cap = _setup(tmp_path)
    claim = store.claim_task(cap, now=100, lease_seconds=1)
    assert claim is not None
    assert store.reclaim_expired(now=102) == 1
    record = store.list_tasks()[0]
    assert record.status == 'uncertain'
    assert record.claim_id == claim.claim_id
    assert store.claim_task(cap, now=103) is None
    assert store.retry_task('task', now=103) is False
    assert store.complete_task('task', 'wrong', result='untrusted', now=103) is False
    assert store.complete_task('task', claim.claim_id, result='reconciled', now=104) is True
    assert store.list_tasks()[0].result == 'reconciled'


def test_runner_renews_and_heartbeats_while_executor_is_blocked(tmp_path, monkeypatch):
    import hermes_cli.fleet_runner as module
    monkeypatch.setattr(module, '_LEASE_REFRESH_SECONDS', 0.01, raising=False)
    store, cap = _setup(tmp_path)
    refreshed = threading.Event()
    entered = threading.Event()
    original = store.renew_claim
    renewals = []

    def renew(*args, **kwargs):
        claim = original(*args, **kwargs)
        renewals.append(claim)
        if entered.is_set():
            refreshed.set()
        return claim

    monkeypatch.setattr(store, 'renew_claim', renew)

    def execute(_argv, _body):
        entered.set()
        assert refreshed.wait(5), 'No lease renewal while execution was in flight'
        return SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'result', 'exit_code': 0, 'text': 'done'}), stderr='')

    runner = FleetRunner('mac', store, 'hermes', ['worker'], ['Hermes Agent'], capabilities=[cap], executor=execute)
    assert runner.run_once()
    assert len(renewals) >= 2
    assert store.list_tasks()[0].status == 'completed'
    assert store.list_runners()[0]['online']


def test_missing_terminal_result_is_uncertain_and_cannot_be_retried(tmp_path):
    store, cap = _setup(tmp_path)
    runner = FleetRunner('mac', store, 'hermes', ['worker'], ['Hermes Agent'], capabilities=[cap],
                         executor=lambda *_: SimpleNamespace(returncode=0, stdout='partial output', stderr=''))
    assert runner.run_once()
    record = store.list_tasks()[0]
    assert record.status == 'uncertain'
    assert 'partial output' in record.result
    assert not store.retry_task('task')
    assert runner.stopped


def test_unacknowledged_result_is_replayed_without_executing_task_again(tmp_path):
    store, cap = _setup(tmp_path)
    calls = []
    original = store.complete_task

    def uncertain_ack(*args, **kwargs):
        assert original(*args, **kwargs)
        raise RuntimeError('response lost after commit')

    store.complete_task = uncertain_ack
    runner = FleetRunner('mac', store, 'hermes', ['worker'], [], capabilities=[cap], state_dir=tmp_path / 'receipts',
                         executor=lambda *_: calls.append('execute') or SimpleNamespace(returncode=0,
                             stdout=json.dumps({'type': 'result', 'text': 'delivered', 'exit_code': 0}), stderr=''))
    import pytest
    with pytest.raises(RuntimeError, match='response lost'):
        runner.run_once()
    assert list((tmp_path / 'receipts').glob('*.delivery.json'))
    store.complete_task = original
    restarted = FleetRunner('mac', store, 'hermes', ['worker'], [], capabilities=[cap], state_dir=tmp_path / 'receipts',
                            executor=lambda *_: calls.append('DUPLICATE'))
    restarted.run_once()
    assert calls == ['execute']
    assert not list((tmp_path / 'receipts').glob('*.delivery.json'))
    assert not list((tmp_path / 'receipts').glob('*/claim.json'))


def test_restarted_runner_reconciles_captured_admission_before_claiming(tmp_path, monkeypatch):
    store, cap = _setup(tmp_path)
    claim = store.claim_task(cap)
    import hashlib
    directory = tmp_path / 'receipts' / hashlib.sha256(claim.claim_id.encode()).hexdigest()
    directory.mkdir(parents=True)
    (directory / 'claim.json').write_text(json.dumps({'task_id': 'task', 'claim_id': claim.claim_id,
                                                    'node_id': 'mac', 'profile': 'worker'}))
    (directory / 'stdout-fixture').write_text(json.dumps({'type': 'system', 'subtype': 'admission',
                                                         'session_id': 'session', 'admission_id': 'admission'}) + '\n')
    observed = []

    def reconcile(self, profile, identity, **kwargs):
        observed.append((profile, identity['admission_id']))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'result', 'text': 'recovered',
                                      'settled': True, 'exit_code': 0}), stderr='', telemetry=None)

    monkeypatch.setattr(FleetRunner, '_reconcile', reconcile)
    runner = FleetRunner('mac', store, 'hermes', ['worker'], [], capabilities=[cap], state_dir=tmp_path / 'receipts',
                         executor=lambda *_: observed.append('DUPLICATE'))
    runner.run_once()
    assert observed == [('worker', 'admission')]
    assert store.list_tasks()[0].status == 'completed'
    assert store.list_tasks()[0].result == 'recovered'


def test_damaged_claim_receipt_holds_matching_execution_without_replay(tmp_path):
    store, cap = _setup(tmp_path)
    claim = store.claim_task(cap)
    import hashlib
    directory = tmp_path / 'receipts' / hashlib.sha256(claim.claim_id.encode()).hexdigest()
    directory.mkdir(parents=True)
    (directory / 'claim.json').write_text('{')
    calls = []
    runner = FleetRunner('mac', store, 'hermes', ['worker'], [], capabilities=[cap], state_dir=tmp_path / 'receipts',
                         executor=lambda *_: calls.append('DUPLICATE'))
    assert not runner.run_once()
    assert not calls
    assert runner.stopped
    assert store.list_tasks()[0].status == 'uncertain'
    assert 'Damaged local claim receipt' in store.list_tasks()[0].error


@pytest.mark.parametrize('restarted', [False, True])
@pytest.mark.parametrize('terminal_error', ['', 'Conversation worktree unavailable'])
def test_failed_reconciled_delivery_preserves_terminal_diagnostic_and_identity(
        tmp_path, monkeypatch, restarted, terminal_error):
    store, cap = _setup(tmp_path)
    identity = {'type': 'system', 'subtype': 'admission', 'session_id': 'session-proof',
                'admission_id': 'admission-proof'}
    terminal = {'type': 'result', 'exit_code': 1, 'settled': True, 'outcome': 'failed',
                'session_id': 'session-proof', 'admission_id': 'admission-proof',
                'text': 'The admitted turn failed.', 'error': terminal_error}
    state_dir = tmp_path / 'receipts'
    if restarted:
        import hashlib
        claim = store.claim_task(cap)
        directory = state_dir / hashlib.sha256(claim.claim_id.encode()).hexdigest()
        directory.mkdir(parents=True)
        (directory / 'claim.json').write_text(json.dumps({'task_id': 'task', 'claim_id': claim.claim_id,
                                                        'node_id': 'mac', 'profile': 'worker'}))
        (directory / 'stdout-fixture').write_text(json.dumps(identity) + '\n')

    monkeypatch.setattr(FleetRunner, '_reconcile', lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout=json.dumps(terminal), stderr='session_id: session-proof\n'))
    runner = FleetRunner('mac', store, 'hermes', ['worker'], [], capabilities=[cap], state_dir=state_dir,
                         executor=lambda *_: SimpleNamespace(returncode=1, stdout=json.dumps(identity), stderr=''))
    runner.run_once()
    record = store.list_tasks()[0]
    assert record.status == 'failed'
    assert record.error.startswith(terminal_error or 'The admitted turn failed.')
    assert 'The admitted turn failed.' in record.error
    assert 'session-proof' in record.error
    assert 'admission-proof' in record.error
