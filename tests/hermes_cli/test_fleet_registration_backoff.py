"""Registration resilience must never replay an uncertain task claim."""
import io
import ssl
import urllib.error

import pytest

from hermes_cli.fleet_client import FleetClient, FleetTransportError
from hermes_cli.fleet_runner import FleetRunner


def _failure(monkeypatch, cause):
    def fail(*_args, **_kwargs):
        raise cause
    with monkeypatch.context() as patch:
        patch.setattr('hermes_cli.fleet_client.open_credentialed_url', fail)
        with pytest.raises(FleetTransportError) as caught:
            FleetClient('http://inert.invalid', token='inert').health()
    return caught.value


class Coordinator:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.registrations = 0
        self.claims = 0
        self.on_claim = lambda: None

    def register_runner(self, *_args, **_kwargs):
        self.registrations += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def heartbeat_runner(self, *_args, **_kwargs):
        return True

    def claim_task(self, *_args, **_kwargs):
        self.claims += 1
        self.on_claim()


def _runner(coordinator, monkeypatch, *, live=lambda: True):
    runner = FleetRunner('inert', coordinator, 'hermes', ['worker'], [],
                         liveness_check=live, executor=lambda *_: pytest.fail('No execution authorized'))
    coordinator.on_claim = runner.stop
    waits = []

    def wait(delay):
        if not runner.stopped:
            assert coordinator.claims == 0
            waits.append(delay)
        return runner.stopped

    monkeypatch.setattr(runner._stop, 'wait', wait)
    return runner, waits


@pytest.mark.parametrize('cause', [TimeoutError('inert timeout'), ConnectionResetError('inert reset'),
                                  urllib.error.URLError(ConnectionRefusedError('inert refused'))])
def test_transient_registration_failures_back_off_before_any_claim(monkeypatch, cause):
    failure = _failure(monkeypatch, cause)
    coordinator = Coordinator([failure, failure, True])
    runner, waits = _runner(coordinator, monkeypatch)
    runner.run_forever()
    assert coordinator.registrations == 3
    assert coordinator.claims == 1
    assert waits == [1.0, 2.0]


def test_registration_retry_budget_is_finite_per_poll_cycle(monkeypatch):
    failure = _failure(monkeypatch, TimeoutError('inert timeout'))
    coordinator = Coordinator([failure] * 3 + [True])
    runner, waits = _runner(coordinator, monkeypatch)
    runner.run_forever()
    assert coordinator.registrations == 4
    assert coordinator.claims == 1
    assert waits == [1.0, 2.0, 5.0]


def test_idle_heartbeat_outage_skips_claim_and_revalidates_next_poll(monkeypatch):
    failure = _failure(monkeypatch, TimeoutError('inert heartbeat outage'))
    coordinator = Coordinator([True, True])
    runner, waits = _runner(coordinator, monkeypatch)
    heartbeats = []

    def heartbeat(*_args, **_kwargs):
        heartbeats.append(coordinator.registrations)
        if len(heartbeats) == 1:
            raise failure
        return True

    coordinator.heartbeat_runner = heartbeat
    runner.run_forever()
    assert heartbeats == [1, 2]
    assert coordinator.registrations == 2 and coordinator.claims == 1
    assert waits == [5.0]


@pytest.mark.parametrize('outcome', ['denied', 'certificate', 'refused', 'stopped', 'owner-gone'])
def test_heartbeat_must_authorize_and_preserve_owner_before_claim(monkeypatch, outcome):
    coordinator = Coordinator([True])
    live = [True]
    runner, waits = _runner(coordinator, monkeypatch, live=lambda: live[0])
    original = runner._is_live

    def is_live():
        result = original()
        if not result:
            runner.stop()
        return result

    monkeypatch.setattr(runner, '_is_live', is_live)

    def heartbeat(*_args, **_kwargs):
        if outcome == 'denied':
            raise _failure(monkeypatch, urllib.error.HTTPError('http://inert.invalid', 401, 'denied', {}, None))
        if outcome == 'certificate':
            raise _failure(monkeypatch, urllib.error.URLError(ssl.SSLCertVerificationError('untrusted')))
        if outcome == 'stopped':
            runner.stop()
        if outcome == 'owner-gone':
            live[0] = False
        return outcome != 'refused'

    coordinator.heartbeat_runner = heartbeat
    if outcome in {'denied', 'certificate', 'refused'}:
        with pytest.raises(FleetTransportError):
            runner.run_forever()
    else:
        runner.run_forever()
    assert coordinator.registrations == 1 and coordinator.claims == 0
    assert waits == []


@pytest.mark.parametrize('cause', [urllib.error.HTTPError('http://inert.invalid', 401, 'denied', {}, None),
                                  urllib.error.URLError(ssl.SSLCertVerificationError('untrusted cert')),
                                  PermissionError('inert denied access')])
def test_authorization_and_certificate_errors_are_not_retried(monkeypatch, cause):
    failure = _failure(monkeypatch, cause)
    coordinator = Coordinator([failure, True])
    runner, waits = _runner(coordinator, monkeypatch)
    with pytest.raises(FleetTransportError):
        runner.run_forever()
    assert coordinator.registrations == 1
    assert coordinator.claims == 0
    assert waits == []


@pytest.mark.parametrize('payload', [b'not-json', b'[]'])
def test_protocol_errors_are_not_retried(monkeypatch, payload):
    with monkeypatch.context() as patch:
        patch.setattr('hermes_cli.fleet_client.open_credentialed_url', lambda *_a, **_k: io.BytesIO(payload))
        with pytest.raises(FleetTransportError) as caught:
            FleetClient('http://inert.invalid', token='inert').health()
    coordinator = Coordinator([caught.value, True])
    runner, waits = _runner(coordinator, monkeypatch)
    with pytest.raises(FleetTransportError):
        runner.run_forever()
    assert coordinator.registrations == 1 and coordinator.claims == 0
    assert waits == []


def test_registration_refusal_never_advances_to_claim(monkeypatch):
    coordinator = Coordinator([False])
    runner, waits = _runner(coordinator, monkeypatch)
    with pytest.raises(FleetTransportError, match='refused'):
        runner.run_forever()
    assert coordinator.registrations == 1 and coordinator.claims == 0
    assert waits == []


@pytest.mark.parametrize('stop', [True, False])
def test_registration_backoff_obeys_stop_and_owner_liveness(monkeypatch, stop):
    failure = _failure(monkeypatch, TimeoutError('inert timeout'))
    coordinator = Coordinator([failure, True])
    live = [True]
    runner, _waits = _runner(coordinator, monkeypatch, live=lambda: live[0])
    def wait(_delay):
        if stop:
            runner.stop()
        live[0] = False
        return stop
    monkeypatch.setattr(runner._stop, 'wait', wait)
    # Inactive ownership is observed on the next loop rather than retried.
    original = runner._is_live
    def is_live():
        result = original()
        if not result:
            runner.stop()
        return result
    monkeypatch.setattr(runner, '_is_live', is_live)
    runner.run_forever()
    assert coordinator.registrations == 1 and coordinator.claims == 0


def test_transient_claim_failure_is_never_retried(monkeypatch):
    failure = _failure(monkeypatch, TimeoutError('ambiguous claim ack'))
    coordinator = Coordinator([True, True])
    runner, waits = _runner(coordinator, monkeypatch)
    def fail_claim():
        raise failure
    coordinator.on_claim = fail_claim
    with pytest.raises(FleetTransportError):
        runner.run_forever()
    assert coordinator.registrations == 1 and coordinator.claims == 1
    assert waits == []
