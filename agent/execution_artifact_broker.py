"""Disabled task-scoped broker adapter, with no concrete service client/launcher.

All injected broker/qualification/plan objects are trusted HOST boundaries, never
worker attestations or model input. Fake receipts only exercise this contract;
separate review must qualify confinement, direct capture and terminal teardown.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import asyncio
import json
import re
import math
import time
from typing import Callable, Protocol
from threading import Lock

from agent.execution_artifact_contracts import (
    Approval, ArtifactDenied, Capture, ConfinedExecutor, Invocation, TaskIdentity, invocation_digest,
)


@dataclass(frozen=True, slots=True)
class TaskResources:
    node: str
    backend: str
    cpu_millis: int
    memory_bytes: int
    processes: int
    scratch_bytes: int
    output_bytes: int
    deadline: float
    concurrency: int


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    identity: TaskIdentity
    service: str
    input_snapshot: str
    image_digest: str
    resources: TaskResources
    writable_areas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BrokerQualification:
    service: str
    node: str
    backend: str
    evidence: str
    expires_at: float
    approved: bool
    running: bool


@dataclass(frozen=True, slots=True)
class BrokerRequest:
    approval: Approval
    invocation: Invocation
    plan: ExecutionPlan
    binding: str


@dataclass(frozen=True, slots=True)
class BrokerReceipt:
    binding: str
    exit_code: int
    settled: bool
    removed: bool


class QualifiedBroker(Protocol):
    """Implementations must enforce exact plan and existing service availability.

    No daemon start, fallback, image pull, credential acquisition or expansion of
    task resources is allowed. run applies deadline/concurrency/quota limits,
    executes exact argv against the immutable snapshot, and streams directly.
    All descendants/handles settle and ephemeral state is removed before success.
    abort_and_settle must prove no further writes or execution; timeout is false.
    """
    qualification: BrokerQualification

    def run(self, request: BrokerRequest, emit: Callable[[str, bytes], None]) -> BrokerReceipt: ...

    def abort_and_settle(self, request: BrokerRequest) -> bool: ...


def _digest(value) -> str:
    return sha256(json.dumps(asdict(value), sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def plan_digest(plan: ExecutionPlan) -> str:
    return _digest(plan)


def _control_kind(error):
    for kind in (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit):
        if isinstance(error, kind):
            return kind
    return None


class BrokerArtifactExecutor(ConfinedExecutor):
    """Host-injected source adapter; default refuses before inspecting the broker.

    HOST approval binds the full plan digest. No host path, environment, mount,
    network or generic shell override exists. Snapshot/image identifiers refer to
    previously approved immutable objects, never a live working tree or host home.
    The host must authenticate and qualify the injected implementation separately.
    """
    def __init__(self, *, enabled=False, broker: QualifiedBroker | None = None,
                 plan: ExecutionPlan | None = None, quarantine=None, clock=time.time):
        if enabled is not True:
            raise ArtifactDenied("execution_artifacts_disabled")
        if broker is None or plan is None or not callable(quarantine):
            raise ArtifactDenied("broker_qualification_missing")
        self._broker, self._plan = broker, plan
        self._quarantine, self._clock = quarantine, clock
        self._quarantined = False

    def _request(self, approval: Approval, invocation: Invocation) -> BrokerRequest:
        if self._quarantined:
            raise ArtifactDenied("broker_task_quarantined")
        plan, now = self._plan, self._clock()
        try:
            approved_plan = plan_digest(plan)
        except (TypeError, ValueError):
            raise ArtifactDenied("broker_plan_invalid") from None
        qualification, resources = self._broker.qualification, plan.resources
        if (approval.identity != plan.identity or approval.invocation_digest != invocation_digest(invocation)
                or not approval.execution_plan_digest or approval.execution_plan_digest != approved_plan
                or not now < approval.expires_at or not now < resources.deadline <= approval.expires_at
                or resources.node != approval.identity.node or resources.backend != "container-broker"
                or not plan.service
                or re.fullmatch(r"sha256:[0-9a-f]{64}", plan.input_snapshot) is None
                or re.fullmatch(r"sha256:[0-9a-f]{64}", plan.image_digest) is None
                or plan.writable_areas != ("scratch", "output")):
            raise ArtifactDenied("broker_scope_or_plan_mismatch")
        limits = (resources.cpu_millis, resources.memory_bytes, resources.processes,
                  resources.scratch_bytes, resources.output_bytes, resources.concurrency)
        if not all(type(value) is int and value > 0 for value in limits):
            raise ArtifactDenied("broker_task_resources_missing")
        if (not isinstance(qualification, BrokerQualification) or not qualification.evidence
                or qualification.approved is not True or qualification.running is not True
                or qualification.service != plan.service or qualification.node != resources.node
                or qualification.backend != resources.backend or not math.isfinite(qualification.expires_at)
                or qualification.expires_at < resources.deadline):
            raise ArtifactDenied("broker_service_unqualified")
        # Bind the complete approval (including audience, request/session/generation),
        # invocation and plan; a receipt for another operation cannot become capture.
        request = BrokerRequest(approval, invocation, plan, "")
        try:
            return BrokerRequest(approval, invocation, plan, _digest(request))
        except (TypeError, ValueError):
            raise ArtifactDenied("broker_request_invalid") from None

    def capture(self, approval: Approval, invocation: Invocation) -> Capture:
        request = self._request(approval, invocation)
        streams = {"stdout": bytearray(), "stderr": bytearray()}
        size, poisoned, accepting = 0, False, True
        stream_lock = Lock()

        def emit(name, chunk):
            nonlocal size, poisoned
            with stream_lock:
                if not accepting or type(name) is not str or name not in streams or type(chunk) is not bytes:
                    poisoned = True
                    raise ArtifactDenied("broker_stream_invalid")
                size += len(chunk)
                if size > min(1_048_576, request.plan.resources.output_bytes):
                    poisoned = True
                    raise ArtifactDenied("broker_capture_overflow")
                streams[name].extend(chunk)

        try:
            receipt = self._broker.run(request, emit)
            with stream_lock:
                accepting = False
                invalid = poisoned
            if (invalid or not isinstance(receipt, BrokerReceipt) or receipt.binding != request.binding
                    or receipt.settled is not True or receipt.removed is not True
                    or type(receipt.exit_code) is not int or not -255 <= receipt.exit_code <= 255
                    or self._clock() >= request.plan.resources.deadline):
                raise ArtifactDenied("broker_capture_unverified")
            return Capture(approval.invocation_id, approval.identity, invocation,
                           bytes(streams["stdout"]), bytes(streams["stderr"]), receipt.exit_code, True, True)
        except BaseException as error:  # health: allow BLE001 -- Cleanup and propagate control cancellation; other broker exceptions may contain raw output and must not be logged.
            control = _control_kind(error)
            try:
                settled = self._broker.abort_and_settle(request) is True
            except BaseException as abort_error:  # health: allow BLE001 -- Abort failure is uncertain settlement; preserve control cancellation without logging broker text.
                settled = False
                control = _control_kind(abort_error) or control
            if not settled:
                self._quarantined = True
                try:
                    self._quarantine(approval.identity)
                except BaseException as quarantine_error:  # health: allow BLE001 -- Preserve control cancellation and fail closed without exposing persistence details.
                    control = _control_kind(quarantine_error) or control
                    if control:
                        raise control() from None
                    raise ArtifactDenied("broker_quarantine_unavailable") from None
                if control:
                    raise control() from None
                raise ArtifactDenied("broker_execution_unsettled") from None
            if control:
                raise control() from None
            raise ArtifactDenied("broker_capture_denied") from None
        finally:
            with stream_lock:
                accepting = False
                for stream in streams.values():
                    stream.clear()
