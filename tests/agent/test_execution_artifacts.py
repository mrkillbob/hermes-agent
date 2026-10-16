"""Synthetic host adapters exercise the disabled artifact producer end to end.

No processes, provider requests, credentials or live provenance are involved.
Adapters below model host trust; they do not prove OS confinement.
"""
from dataclasses import replace
from threading import Barrier, RLock
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from agent.execution_artifact_contracts import (
    Approval, ApprovalAuthority, ArtifactDenied, ArtifactSink, Capture,
    ConfinedExecutor, Invocation, Recipient, TaskIdentity, invocation_digest,
)
from agent.execution_artifacts import ArtifactReference, ExecutionArtifactRegistry


IDENTITY = TaskIdentity("task", "admission", "node", "profile", "session", "turn", "policy")
INVOCATION = Invocation("synthetic-test", "digest", ("--report",), "cwd-inode", "project-inode", "test")
RECIPIENT = Recipient("provider", "synthetic-account", "https://example.invalid/v1", "synthetic-model")


class Authority(ApprovalAuthority):
    def __init__(self):
        self.revoked = False
        self.calls = 0
        self.approval = Approval(IDENTITY, invocation_digest(INVOCATION), RECIPIENT, "approval", "request", "invocation-request", 1, 200., "test")
        self.lock = RLock()
        self.used = set()
        self.size = 0

    def approve(self, identity, invocation, recipient, request_id):
        self.calls += 1
        return replace(self.approval, request_id=request_id, invocation_id=f"invocation-{request_id}")

    def current(self, approval):
        return not self.revoked and approval.generation == self.approval.generation

    @contextmanager
    def dispatch_scope(self, approval, byte_count):
        with self.lock:
            if not self.current(approval) or approval.request_id in self.used:
                raise ArtifactDenied("authority_refused")
            if len(self.used) >= approval.request_budget or self.size + byte_count > approval.export_budget:
                raise ArtifactDenied("authority_budget")
            self.used.add(approval.request_id)
            self.size += byte_count
            yield


class Executor(ConfinedExecutor):
    def __init__(self):
        self.calls = 0
        self.result = Capture("invocation", IDENTITY, INVOCATION,
                              b"Tests passed: 3\n", b"", 0, True, True)

    def capture(self, approval, invocation):
        self.calls += 1
        return replace(self.result, invocation_id=approval.invocation_id)


class Sink(ArtifactSink):
    def __init__(self, recipient=RECIPIENT):
        self._recipient = recipient
        self.sent = []
        self.fail = False

    @property
    def recipient(self):
        return self._recipient

    def send(self, request_id, payload):
        self.sent.append((request_id, payload))
        if self.fail:
            raise OSError("synthetic uncertain receipt")


@pytest.fixture
def pipeline():
    authority, executor, sink = Authority(), Executor(), Sink()
    registry = ExecutionArtifactRegistry(enabled=True, authority=authority, executor=executor,
                                         clock=lambda: 100.)
    return registry, authority, executor, sink


def produce(registry, request_id="request"):
    return registry.produce(IDENTITY, INVOCATION, RECIPIENT, request_id=request_id)


def dispatch(registry, ref, sink, **kwargs):
    registry.dispatch(ref, identity=kwargs.get("identity", IDENTITY),
                      invocation=kwargs.get("invocation", INVOCATION),
                      request_id=kwargs.get("request_id", "request"), sink=sink)


def test_default_denies_before_approval_or_execution():
    authority, executor = Authority(), Executor()
    registry = ExecutionArtifactRegistry(authority=authority, executor=executor)
    with pytest.raises(ArtifactDenied, match="disabled"):
        produce(registry)
    assert (authority.calls, executor.calls) == (0, 0)


def test_exact_failed_test_result_reaches_inert_sink_and_is_consumed(pipeline):
    registry, _, executor, sink = pipeline
    executor.result = replace(executor.result, exit_code=1, stderr=b"Expected 2, got 3\n")
    ref = produce(registry)
    dispatch(registry, ref, sink)
    assert json.loads(sink.sent[0][1]) == {
        "kind": "test", "exit_code": 1, "stdout": "Tests passed: 3\n", "stderr": "Expected 2, got 3\n"}
    assert ref.handle.encode() not in sink.sent[0][1]
    with pytest.raises(ArtifactDenied):
        dispatch(registry, ref, sink, request_id="another")
    assert len(sink.sent) == 1


@pytest.mark.parametrize("field", ["task", "admission", "node", "profile", "session", "turn", "policy"])
def test_every_task_binding_is_required_at_release(pipeline, field):
    registry, _, _, sink = pipeline
    with pytest.raises(ArtifactDenied, match="binding"):
        dispatch(registry, produce(registry), sink, identity=replace(IDENTITY, **{field: "foreign"}))
    assert not sink.sent


@pytest.mark.parametrize("field", ["kind", "account", "endpoint", "model"])
def test_recipient_does_not_authorize_another_audience(pipeline, field):
    registry, _, _, _ = pipeline
    sink = Sink(replace(RECIPIENT, **{field: "foreign"}))
    with pytest.raises(ArtifactDenied, match="binding"):
        dispatch(registry, produce(registry), sink)
    assert not sink.sent


@pytest.mark.parametrize("field,value", [
    ("executable", "foreign"), ("executable_digest", "changed"), ("argv", ("--different",)),
    ("cwd_identity", "foreign"), ("project_identity", "foreign"), ("operation", "shell"),
])
def test_exact_invocation_required(pipeline, field, value):
    registry, _, _, sink = pipeline
    with pytest.raises(ArtifactDenied, match="binding"):
        dispatch(registry, produce(registry), sink, invocation=replace(INVOCATION, **{field: value}))
    assert not sink.sent


@pytest.mark.parametrize("changes", [
    {"settled": False}, {"confined": False}, {"overflow": True}, {"confined": "yes"},
    {"stdout": b"\xff"}, {"stdout": b"\x00hidden"}, {"stdout": b"x" * 1_048_577},
    {"identity": replace(IDENTITY, node="foreign")},
    {"invocation": replace(INVOCATION, argv=("--changed",))},
])
def test_unverified_capture_never_registers_or_sends(pipeline, changes):
    registry, _, executor, sink = pipeline
    executor.result = replace(executor.result, **changes)
    with pytest.raises((ArtifactDenied, ValueError)):
        produce(registry)
    assert not sink.sent


def test_new_output_secrets_are_filtered_and_raw_bytes_stay_off_wire(pipeline):
    registry, _, executor, sink = pipeline
    secret = "sk-" + "aB3cD4eF5gH6iJ7kL8mN9pQ0rS1tU2vW3xY4zA5b"
    executor.result = replace(executor.result, stdout=f"API_KEY={secret}\n".encode())
    with pytest.raises(ValueError, match="secret_detected"):
        produce(registry)
    assert not sink.sent


def test_revocation_expiry_and_forged_handle_fail_closed(pipeline):
    registry, authority, _, sink = pipeline
    ref = produce(registry)
    authority.revoked = True
    with pytest.raises(ArtifactDenied):
        dispatch(registry, ref, sink)
    authority.revoked = False
    registry._clock = lambda: 201.
    with pytest.raises(ArtifactDenied):
        dispatch(registry, ref, sink)
    with pytest.raises(ArtifactDenied):
        dispatch(registry, ArtifactReference("fabricated"), sink)
    assert not sink.sent


def test_uncertain_send_consumes_release_without_retry(pipeline):
    registry, _, executor, sink = pipeline
    ref = produce(registry)
    sink.fail = True
    with pytest.raises(OSError):
        dispatch(registry, ref, sink)
    with pytest.raises(ArtifactDenied):
        dispatch(registry, ref, sink)
    assert len(sink.sent) == executor.calls == 1


def test_atomic_request_budget_with_concurrent_releases(pipeline):
    registry, authority, _, sink = pipeline
    authority.approval = replace(authority.approval, request_budget=1)
    refs = [produce(registry, "request-0"), produce(registry, "request-1")]
    barrier = Barrier(2)

    def release(index):
        barrier.wait(timeout=5)
        try:
            dispatch(registry, refs[index], sink, request_id=f"request-{index}")
            return True
        except ArtifactDenied:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(release, range(2)))
    assert sorted(results) == [False, True]
    assert len(sink.sent) == 1


def test_clearing_turn_discards_result_and_restart_cannot_recover_authority(pipeline):
    registry, _, _, sink = pipeline
    ref = produce(registry)
    registry.clear_turn(IDENTITY.turn)
    with pytest.raises(ArtifactDenied):
        dispatch(registry, ref, sink)
    with pytest.raises(ArtifactDenied):
        ExecutionArtifactRegistry().dispatch(ref, identity=IDENTITY, invocation=INVOCATION,
                                              request_id="request", sink=sink)
    assert not sink.sent


def test_request_lifecycle_clears_artifact_and_closes_further_execution(pipeline):
    registry, _, executor, sink = pipeline
    ref = produce(registry)
    registry.clear_request("request")
    with pytest.raises(ArtifactDenied):
        dispatch(registry, ref, sink)
    with pytest.raises(ArtifactDenied):
        produce(registry)
    assert executor.calls == 1
    assert not sink.sent


def test_request_closure_during_capture_does_not_create_authority(pipeline):
    registry, _, executor, sink = pipeline
    original = executor.capture

    def closing_capture(approval, invocation):
        result = original(approval, invocation)
        registry.clear_request("request")
        return result

    executor.capture = closing_capture
    with pytest.raises(ArtifactDenied):
        produce(registry)
    assert not sink.sent


def test_request_binding_and_uncertain_execution_are_single_use(pipeline):
    registry, _, executor, sink = pipeline
    ref = produce(registry)
    with pytest.raises(ArtifactDenied):
        dispatch(registry, ref, sink, request_id="foreign-request")
    with pytest.raises(ArtifactDenied):
        produce(registry)
    assert executor.calls == 1
    assert not sink.sent


def test_task_budget_survives_admission_refresh(pipeline):
    registry, authority, _, sink = pipeline
    authority.approval = replace(authority.approval, request_budget=1)
    dispatch(registry, produce(registry), sink)
    fresh = replace(IDENTITY, admission="new-admission")
    authority.approval = replace(authority.approval, identity=fresh)
    executor = Executor()
    executor.result = replace(executor.result, identity=fresh)
    registry._executor = executor
    ref = registry.produce(fresh, INVOCATION, RECIPIENT, request_id="next")
    with pytest.raises(ArtifactDenied, match="task_budget"):
        dispatch(registry, ref, sink, identity=fresh, request_id="next")
    assert len(sink.sent) == 1


def test_raw_capture_representation_does_not_retain_output_in_logs():
    capture = Capture("invocation", IDENTITY, INVOCATION, b"private stdout", b"private stderr", 0, True, True)
    assert "private stdout" not in repr(capture)
    assert "private stderr" not in repr(capture)
    assert "--report" not in repr(capture)
