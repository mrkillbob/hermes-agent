"""Inert broker contract tests; receipts do not qualify an OS boundary."""
from dataclasses import replace
import pytest

from agent.execution_artifact_contracts import ArtifactDenied
from tests.agent.test_execution_artifacts import Authority, INVOCATION


def fixture_broker():
    from agent.execution_artifact_broker import (
        BrokerArtifactExecutor, BrokerQualification, ExecutionPlan, TaskResources, plan_digest,
        BrokerReceipt,
    )
    approval = Authority().approval
    resources = TaskResources("node", "container-broker", 1000, 64_000_000, 8, 4096, 4096, 150., 1)
    plan = ExecutionPlan(approval.identity, "service", "sha256:" + "a" * 64, "sha256:" + "b" * 64, resources,
                         ("scratch", "output"))
    approval = replace(approval, execution_plan_digest=plan_digest(plan))

    class Broker:
        qualification = BrokerQualification("service", "node", "container-broker", "qualification", 200., True, True)
        def __init__(self):
            self.calls = 0
            self.settled = True
            self.chunks = [("stdout", b"Tests passed\n")]
            self.receipt_change = {}
        def run(self, request, emit):
            self.calls += 1
            for stream, chunk in self.chunks:
                emit(stream, chunk)
            return replace(BrokerReceipt(request.binding, 0, True, True), **self.receipt_change)
        def abort_and_settle(self, request):
            return self.settled
    broker = Broker()
    quarantined = []
    executor = BrokerArtifactExecutor(broker=broker, plan=plan, enabled=True,
                                      quarantine=lambda i: quarantined.append(i), clock=lambda: 100.)
    return executor, broker, approval, plan, quarantined


def test_default_broker_refuses_without_calling_service():
    from agent.execution_artifact_broker import BrokerArtifactExecutor
    with pytest.raises(ArtifactDenied, match="disabled"):
        BrokerArtifactExecutor()


def test_exact_bound_capture_and_per_task_resources():
    executor, broker, approval, plan, _ = fixture_broker()
    result = executor.capture(approval, INVOCATION)
    assert result.stdout == b"Tests passed\n"
    assert result.invocation_id == approval.invocation_id
    assert result.identity == plan.identity
    assert result.confined and result.settled
    assert broker.calls == 1


@pytest.mark.parametrize("change", [
    {"identity": replace(Authority().approval.identity, session="foreign")},
    {"execution_plan_digest": "foreign"},
    {"invocation_digest": "foreign"},
    {"expires_at": 99.},
])
def test_foreign_or_expired_scope_never_runs(change):
    executor, broker, approval, _, _ = fixture_broker()
    with pytest.raises(ArtifactDenied):
        executor.capture(replace(approval, **change), INVOCATION)
    assert broker.calls == 0


@pytest.mark.parametrize("change", [{"running": False}, {"approved": False}, {"node": "foreign"},
                                    {"backend": "native"}, {"expires_at": 99.}])
def test_missing_existing_qualified_service_refuses_without_allocation(change):
    executor, broker, approval, _, _ = fixture_broker()
    broker.qualification = replace(broker.qualification, **change)
    with pytest.raises(ArtifactDenied):
        executor.capture(approval, INVOCATION)
    assert broker.calls == 0


def test_overflow_aborts_and_uncertain_cleanup_quarantines():
    executor, broker, approval, _, quarantined = fixture_broker()
    broker.chunks = [("stdout", b"x" * 1_048_577)]
    broker.settled = False
    with pytest.raises(ArtifactDenied, match="unsettled"):
        executor.capture(approval, INVOCATION)
    assert quarantined == [approval.identity]


@pytest.mark.parametrize("change", [{"binding": "foreign"}, {"settled": False}, {"removed": False}])
def test_receipt_mismatch_or_unsettled_descendants_never_yields_capture(change):
    executor, broker, approval, _, _ = fixture_broker()
    broker.receipt_change = change
    with pytest.raises(ArtifactDenied):
        executor.capture(approval, INVOCATION)


def test_no_cross_host_execution_plan_substitution():
    executor, broker, approval, plan, _ = fixture_broker()
    executor._plan = replace(plan, resources=replace(plan.resources, node="another-host"))
    with pytest.raises(ArtifactDenied):
        executor.capture(approval, INVOCATION)
    assert broker.calls == 0


def test_broker_cannot_swallow_overflow_and_return_partial_success():
    executor, broker, approval, _, _ = fixture_broker()
    from agent.execution_artifact_broker import BrokerReceipt
    def swallow(request, emit):
        try:
            emit("stdout", b"x" * 1_048_577)
        except ArtifactDenied:
            pass
        return BrokerReceipt(request.binding, 0, True, True)
    broker.run = swallow
    with pytest.raises(ArtifactDenied):
        executor.capture(approval, INVOCATION)


def test_quarantine_callback_failure_tombstones_executor_without_raw_error():
    executor, broker, approval, _, _ = fixture_broker()
    broker.chunks = [("stdout", b"x" * 1_048_577)]
    broker.settled = False
    def fail(_):
        raise RuntimeError("synthetic-private-detail")
    executor._quarantine = fail
    with pytest.raises(ArtifactDenied, match="quarantine_unavailable"):
        executor.capture(approval, INVOCATION)
    calls = broker.calls
    with pytest.raises(ArtifactDenied, match="quarantined"):
        executor.capture(approval, INVOCATION)
    assert broker.calls == calls


def test_mutable_image_label_is_not_immutable_plan():
    from agent.execution_artifact_broker import plan_digest
    executor, broker, approval, plan, _ = fixture_broker()
    executor._plan = replace(plan, image_digest="latest")
    approval = replace(approval, execution_plan_digest=plan_digest(executor._plan))
    with pytest.raises(ArtifactDenied):
        executor.capture(approval, INVOCATION)
    assert broker.calls == 0


def test_control_cancellation_is_propagated_after_verified_abort():
    executor, broker, approval, _, _ = fixture_broker()
    def cancel(request, emit):
        raise KeyboardInterrupt
    broker.run = cancel
    with pytest.raises(KeyboardInterrupt):
        executor.capture(approval, INVOCATION)


def test_bytes_subclass_cannot_lie_about_capture_length():
    executor, broker, approval, _, _ = fixture_broker()
    class UntruthfulBytes(bytes):
        def __len__(self):
            return 0
    broker.chunks = [("stdout", UntruthfulBytes(b"x" * 1_048_577))]
    with pytest.raises(ArtifactDenied):
        executor.capture(approval, INVOCATION)


@pytest.mark.parametrize("phase", ["abort", "quarantine"])
def test_control_cancellation_first_arriving_during_cleanup_is_preserved(phase):
    executor, broker, approval, _, _ = fixture_broker()
    broker.chunks = [("stdout", b"x" * 1_048_577)]
    broker.settled = False
    def interrupt(*args):
        raise KeyboardInterrupt
    if phase == "abort":
        broker.abort_and_settle = interrupt
    else:
        executor._quarantine = interrupt
    with pytest.raises(KeyboardInterrupt):
        executor.capture(approval, INVOCATION)
    with pytest.raises(ArtifactDenied, match="quarantined"):
        executor.capture(approval, INVOCATION)


def test_broker_cannot_swallow_invalid_stream_name_and_return_success():
    from agent.execution_artifact_broker import BrokerReceipt
    executor, broker, approval, _, _ = fixture_broker()
    def swallow(request, emit):
        try:
            emit([], b"lost output")
        except (TypeError, ArtifactDenied):
            pass
        return BrokerReceipt(request.binding, 0, True, True)
    broker.run = swallow
    with pytest.raises(ArtifactDenied):
        executor.capture(approval, INVOCATION)
