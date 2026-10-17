"""Inert regressions for claim polling, response disconnects and diagnostics."""

import io
import logging
import socket
import subprocess
import threading
from types import SimpleNamespace

import pytest

from hermes_cli.fleet_client import FleetTransportError, FleetTransientTransportError
from hermes_cli.fleet_protocol import FleetTask, RunnerCapability, TaskRequirement
from hermes_cli.fleet_runner import FleetRunner
from hermes_cli.fleet_server import FleetServer, _FleetHandler
from hermes_cli.fleet_store import FleetStore, TaskClaim


@pytest.fixture(autouse=True)
def inert_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))

    def refuse(*_args, **_kwargs):
        raise AssertionError("real socket or subprocess forbidden in inert fleet regressions")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr("hermes_cli.fleet_client.open_credentialed_url", refuse)


class Coordinator:
    def __init__(self, error):
        self.error = error
        self.calls = []

    def register_runner(self, *_args, **_kwargs):
        self.calls.append("register")
        return True

    def heartbeat_runner(self, *_args, **_kwargs):
        self.calls.append("heartbeat")
        return True

    def claim_task(self, *_args, **_kwargs):
        self.calls.append("claim")
        raise self.error

    def __getattr__(self, name):
        raise AssertionError(f"unexpected coordinator operation: {name}")


def runner_for(coordinator, **kwargs):
    executor = kwargs.pop("executor", lambda *_args: pytest.fail("claim failure must not launch work"))
    return FleetRunner(
        "node", coordinator, "inert-hermes", ["profile"], [],
        capabilities=[RunnerCapability("node", "profile", platform="inert")],
        executor=executor,
        **kwargs,
    )


def test_claim_transport_failure_defers_without_work_or_journal(tmp_path, caplog):
    coordinator = Coordinator(FleetTransientTransportError("private transport detail"))
    runner = runner_for(coordinator, state_dir=tmp_path)
    with caplog.at_level(logging.WARNING):
        assert runner.run_once(now=100) is False
    assert coordinator.calls == ["heartbeat", "claim"]
    assert list(tmp_path.iterdir()) == []
    assert not runner.stopped
    assert "claim unavailable" in caplog.text
    assert "private transport detail" not in caplog.text


def test_poll_loop_continues_after_claim_transport_failure():
    coordinator = Coordinator(FleetTransientTransportError("unavailable"))
    runner = runner_for(coordinator)

    class TwoPolls:
        polls = 0

        def is_set(self):
            return self.polls >= 2

        def wait(self, _interval):
            self.polls += 1

    runner._stop = TwoPolls()
    runner.run_forever(interval=0)
    assert coordinator.calls == ["register", "heartbeat", "claim"] * 2


def test_nontransient_claim_failure_still_escapes():
    coordinator = Coordinator(FleetTransportError("rejected"))
    with pytest.raises(FleetTransportError, match="rejected"):
        runner_for(coordinator).run_once()
    assert coordinator.calls == ["heartbeat", "claim"]


def test_lost_claim_ack_retains_exact_lease_until_uncertain(tmp_path):
    store = FleetStore(tmp_path / "fleet.db")
    capability = RunnerCapability("node", "profile", platform="inert")
    task = FleetTask("task", "title", "body", TaskRequirement(), "inert-task")
    store.submit_task(task, now=100)
    store.register_runner(capability, now=100)

    class LostAck:
        def heartbeat_runner(self, *_args, **_kwargs):
            return True

        def claim_task(self, capability, **kwargs):
            self.claim = store.claim_task(capability, **kwargs)
            raise FleetTransientTransportError("lost ACK")

    coordinator = LostAck()
    assert runner_for(coordinator).run_once(now=101) is False
    record = store.list_tasks()[0]
    assert (record.status, record.claim_id, record.attempt) == ("running", coordinator.claim.claim_id, 1)
    assert store.claim_task(capability, now=102) is None
    store.reclaim_expired(now=coordinator.claim.lease_expires_at + 1)
    record = store.list_tasks()[0]
    assert (record.status, record.claim_id, record.attempt) == ("uncertain", coordinator.claim.claim_id, 1)


def test_postclaim_delivery_failure_keeps_receipt_and_escapes(tmp_path):
    coordinator = Coordinator(FleetTransientTransportError("completion ACK lost"))
    completed = []

    def complete(task_id, claim_id, **kwargs):
        completed.append((task_id, claim_id))
        raise coordinator.error

    coordinator.complete_task = complete
    runner = runner_for(coordinator, state_dir=tmp_path)
    payload = {"task_id": "task", "claim_id": "claim", "status": "completed",
               "result": "done", "error": "", "telemetry": None}
    runner._save_delivery(payload)
    with pytest.raises(FleetTransientTransportError, match="completion ACK lost"):
        runner.run_once()
    assert completed == [("task", "claim")]
    assert runner._delivery_path(payload).is_file()
    assert coordinator.calls == []


def test_completion_ack_failure_after_execution_is_not_replayed(tmp_path):
    class AfterClaim(Coordinator):
        def claim_task(self, *_args, **_kwargs):
            return TaskClaim("task", "claim", "node", "profile", 200, 1)

        def renew_claim(self, *_args, **_kwargs):
            return TaskClaim("task", "claim", "node", "profile", 200, 1)

        def list_tasks(self):
            return [{"task_id": "task", "body": "inert", "requirement": {"models": []}}]

        def complete_task(self, task_id, claim_id, **_kwargs):
            self.calls.append(("complete", task_id, claim_id))
            raise self.error

    coordinator = AfterClaim(FleetTransientTransportError("completion ACK lost"))
    executions = []

    def execute(*_args):
        executions.append("executed")
        return SimpleNamespace(returncode=0, stdout='{"type":"result","text":"done"}', stderr="")

    runner = runner_for(coordinator, state_dir=tmp_path, executor=execute)
    with pytest.raises(FleetTransientTransportError, match="completion ACK lost"):
        runner.run_once(now=100)
    assert executions == ["executed"]
    assert coordinator.calls.count(("complete", "task", "claim")) == 1
    assert len(list(tmp_path.glob("*.delivery.json"))) == 1
    assert len(list(tmp_path.glob("*/claim.json"))) == 1


def test_false_liveness_is_rate_limited_at_all_entrypoints(monkeypatch, caplog):
    coordinator = Coordinator(FleetTransientTransportError("unused"))
    runner = runner_for(coordinator, liveness_check=lambda: False)
    clock = [100.0]
    monkeypatch.setattr("hermes_cli.fleet_runner._monotonic", lambda: clock[0], raising=False)
    with caplog.at_level(logging.WARNING):
        assert runner.register() is False
        assert runner.run_once() is False
        clock[0] += 299
        assert runner.register() is False
        assert len(caplog.records) == 1
        clock[0] += 1
        assert runner.run_once() is False
    assert len(caplog.records) == 2
    assert "liveness" in caplog.text
    assert coordinator.calls == []


def handler_for(error=None, *, fail_headers=False):
    fleet = object.__new__(FleetServer)
    fleet._write_failure_lock = threading.Lock()
    fleet.response_write_failures = 0
    handler = object.__new__(_FleetHandler)
    handler.server = SimpleNamespace(fleet_server=fleet)
    handler.command = "POST"
    handler.path = "/v1/fleet/tasks/claim?private-query"
    handler.close_connection = False
    handler.statuses = []

    class Output(io.BytesIO):
        def write(self, data):
            if error is not None:
                raise error
            return super().write(data)

    handler.wfile = Output()
    handler.send_response = handler.statuses.append
    handler.send_header = lambda *_args: None

    def end_headers():
        if fail_headers:
            raise error

    handler.end_headers = end_headers
    handler._authorized = lambda: True
    return handler


@pytest.mark.parametrize("error", [BrokenPipeError(), ConnectionResetError()])
@pytest.mark.parametrize("fail_headers", [False, True])
def test_post_disconnect_does_not_attempt_a_second_response(error, fail_headers, monkeypatch, caplog):
    handler = handler_for(error, fail_headers=fail_headers)
    handler._dispatch_post = lambda _path: handler._respond(200, {"private-body": "secret"})
    monkeypatch.setattr("hermes_cli.fleet_server._monotonic", lambda: 10.0, raising=False)
    with caplog.at_level(logging.WARNING):
        handler.do_POST()
    assert handler.statuses == [200]
    assert handler.close_connection
    assert handler.fleet.response_write_failures == 1
    assert len(caplog.records) == 1
    assert "write_failed=True" in caplog.text
    assert "private" not in caplog.text
    assert "secret" not in caplog.text


def test_other_write_errors_are_not_swallowed():
    handler = handler_for(OSError("disk or programming failure"))
    with pytest.raises(OSError, match="programming failure"):
        handler._respond(200, {})


def test_slow_request_diagnostic_contains_only_bounded_metadata(monkeypatch, caplog):
    handler = handler_for()
    handler.command = "GET"
    handler.path = "/v1/fleet/health?token=private-query"
    clock = iter([10.0, 12.5])
    monkeypatch.setattr("hermes_cli.fleet_server._monotonic", lambda: next(clock), raising=False)
    monkeypatch.setattr("hermes_cli.fleet_server.threading.active_count", lambda: 7)
    with caplog.at_level(logging.WARNING):
        handler.do_GET()
    assert handler.statuses == [200]
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "GET health status=200" in message
    assert "2.500s" in message
    assert "write_failed=False" in message
    assert "active_threads=7" in message
    assert "private" not in message
    assert "token" not in message


def test_fast_success_does_not_log_per_poll(monkeypatch, caplog):
    handler = handler_for()
    handler.command = "GET"
    handler.path = "/v1/fleet/health"
    monkeypatch.setattr("hermes_cli.fleet_server._monotonic", lambda: 10.0, raising=False)
    with caplog.at_level(logging.WARNING):
        handler.do_GET()
    assert handler.statuses == [200]
    assert caplog.records == []


def test_disconnected_claim_response_preserves_committed_store_claim(tmp_path, monkeypatch):
    handler = handler_for(BrokenPipeError())
    store = FleetStore(tmp_path / "fleet.db")
    capability = RunnerCapability("node", "profile", platform="inert")
    store.register_runner(capability, now=100)
    store.submit_task(FleetTask("task", "title", "body", TaskRequirement(), "inert-task"), now=100)
    handler.fleet.store = store
    handler._json_body = lambda: {"capability": capability.to_dict(), "now": 101}
    monkeypatch.setattr("hermes_cli.fleet_server._monotonic", lambda: 10.0, raising=False)
    handler.do_POST()
    assert handler.statuses == [200]
    record = store.list_tasks()[0]
    assert record.status == "running"
    assert record.claim_id
    assert record.attempt == 1
    assert store.claim_task(capability, now=102) is None


def test_timing_wrapper_preserves_unauthorized_and_unknown_routes(monkeypatch, caplog):
    handler = handler_for()
    handler.headers = {"Authorization": "Bearer wrong"}
    handler.fleet.token = "inert-test-token"
    del handler._authorized
    handler.command = "GET"
    handler.path = "/private-node/private-profile?token=private-query"
    clock = iter([10.0, 12.0, 13.0, 15.0])
    monkeypatch.setattr("hermes_cli.fleet_server._monotonic", lambda: next(clock), raising=False)
    with caplog.at_level(logging.WARNING):
        handler.do_GET()
        handler.headers = {"Authorization": "Bearer inert-test-token"}
        handler.do_GET()
    assert handler.statuses == [401, 404]
    assert all("GET other" in record.getMessage() for record in caplog.records)
    assert "private" not in caplog.text
    assert "inert-test-token" not in caplog.text
