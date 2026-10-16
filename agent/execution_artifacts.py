"""Disabled-by-default execution-result producer and request-scoped registry.

There is deliberately no runtime consumer, config switch or production adapter.
Host-only adapters must undergo separate confinement/activation review. Tests
exercise the complete pipeline with synthetic approvals/captures and inert sinks.
Raw terminal strings never acquire file-source grants or bypass the firewall.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from threading import RLock
import time
import uuid

from agent.execution_artifact_contracts import (
    Approval, ApprovalAuthority, ArtifactDenied, ArtifactSink, Capture,
    ConfinedExecutor, Invocation, Recipient, TaskIdentity, invocation_digest,
)
from agent.llm_egress_firewall import validate_sanitized_text
from agent.redact import redact_sensitive_text
from agent.source_provenance import SourceProvenanceRegistry


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """Opaque host-local reference, never serialized into provider messages."""
    handle: str


@dataclass(frozen=True, slots=True)
class _Artifact:
    approval: Approval
    invocation_id: str
    raw_digest: str
    presentation_digest: str
    payload: bytes = field(repr=False)


def _identities_valid(identity: TaskIdentity) -> bool:
    return all(isinstance(value, str) and bool(value) for value in (
        identity.task, identity.admission, identity.node, identity.profile,
        identity.session, identity.turn, identity.policy,
    ))


def _validate_approval(approval, identity, invocation, recipient, request_id, now):
    if not isinstance(approval, Approval) or (
        approval.identity != identity or approval.invocation_digest != invocation_digest(invocation)
        or approval.recipient != recipient or not approval.approval_id
        or not request_id or approval.request_id != request_id or not approval.invocation_id
        or type(approval.generation) is not int or approval.generation < 0
        or not math.isfinite(approval.expires_at)
        or not now < approval.expires_at <= now + 86_400
        or approval.result_kind not in {"test", "build", "lint", "repository", "diagnostic"}
        or type(approval.export_budget) is not int or not 0 < approval.export_budget <= 1_048_576
        or type(approval.request_budget) is not int or not 0 < approval.request_budget <= 10
    ):
        raise ArtifactDenied("approval_mismatch")
    if not _identities_valid(identity) or recipient.kind not in {"provider", "owner"}:
        raise ArtifactDenied("identity_missing")
    if not all((recipient.account, recipient.endpoint, recipient.model)):
        raise ArtifactDenied("recipient_missing")
    if not isinstance(invocation.argv, tuple) or not all(isinstance(x, str) for x in invocation.argv):
        raise ArtifactDenied("structured_argv_required")
    if not all((invocation.executable, invocation.executable_digest, invocation.cwd_identity,
                invocation.project_identity, invocation.operation)):
        raise ArtifactDenied("invocation_missing")
    # Secret-like arguments are denied rather than retained or exported.
    for arg in invocation.argv:
        if redact_sensitive_text(arg, force=True, redact_url_credentials=True) != arg:
            raise ArtifactDenied("sensitive_argument")


def _presentation(capture: Capture, result_kind: str) -> bytes:
    if type(capture.exit_code) is not int or not -255 <= capture.exit_code <= 255:
        raise ArtifactDenied("exit_status_invalid")
    if (capture.settled is not True or capture.confined is not True
            or capture.overflow is not False or not capture.invocation_id):
        raise ArtifactDenied("capture_unverified")
    if not isinstance(capture.stdout, bytes) or not isinstance(capture.stderr, bytes):
        raise ArtifactDenied("capture_not_bytes")
    if len(capture.stdout) + len(capture.stderr) > 1_048_576:
        raise ArtifactDenied("capture_size")
    streams = {}
    for name, raw in (("stdout", capture.stdout), ("stderr", capture.stderr)):
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ArtifactDenied("capture_not_utf8") from None
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise ArtifactDenied("capture_control_character")
        text = redact_sensitive_text(text, force=True, file_read=True, redact_url_credentials=True)
        validate_sanitized_text(text, max_bytes=32_768)
        streams[name] = text
    payload = json.dumps({"kind": result_kind, "exit_code": capture.exit_code, **streams},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    validate_sanitized_text(payload.decode("utf-8"), max_bytes=32_768)
    return payload


class ExecutionArtifactRegistry(SourceProvenanceRegistry):
    """Reuse the source registry lifecycle, with distinct execution-artifact authority.

    In-process adapters are trusted code per SECURITY.md; ABCs are not a sandbox
    or proof of OS confinement. Nothing in ordinary agent/tool dispatch instantiates
    this registry. Default construction refuses even to call an approval adapter.
    """
    def __init__(self, *, enabled=False, authority: ApprovalAuthority | None = None,
                 executor: ConfinedExecutor | None = None, clock=time.time):
        super().__init__()
        self._enabled = enabled is True
        self._authority = authority
        self._executor = executor
        self._clock = clock
        self._artifact_lock = RLock()
        self._artifacts: dict[str, _Artifact] = {}
        self._budgets: dict[tuple, tuple[int, int]] = {}
        self._spent_requests: set[tuple] = set()
        self._invocations: set[tuple] = set()
        self._closed_turns: set[str] = set()
        self._closed_requests: set[str] = set()

    def produce(self, identity: TaskIdentity, invocation: Invocation,
                recipient: Recipient, *, request_id: str) -> ArtifactReference:
        """Execute a newly approved operation once; never ingest saved denied text."""
        if not self._enabled or self._authority is None or self._executor is None:
            raise ArtifactDenied("execution_artifacts_disabled")
        approval = self._authority.approve(identity, invocation, recipient, request_id)
        _validate_approval(approval, identity, invocation, recipient, request_id, self._clock())
        if not self._authority.current(approval):
            raise ArtifactDenied("approval_revoked")
        with self._artifact_lock:
            key = (identity, approval.invocation_id)
            if (identity.turn in self._closed_turns or request_id in self._closed_requests
                    or key in self._invocations):
                raise ArtifactDenied("invocation_reused_or_closed")
            # Consume before fallible execution; an uncertain result cannot retry.
            self._invocations.add(key)
        capture = self._executor.capture(approval, invocation)
        if (not isinstance(capture, Capture) or capture.identity != identity
                or capture.invocation != invocation or capture.invocation_id != approval.invocation_id):
            raise ArtifactDenied("capture_binding_mismatch")
        payload = _presentation(capture, approval.result_kind)
        raw_digest = sha256(json.dumps([sha256(capture.stdout).hexdigest(),
                                       sha256(capture.stderr).hexdigest()]).encode()).hexdigest()
        with self._artifact_lock:
            if (identity.turn in self._closed_turns or request_id in self._closed_requests
                    or not self._authority.current(approval)):
                raise ArtifactDenied("approval_revoked")
            handle = uuid.uuid4().hex
            self._artifacts[handle] = _Artifact(approval, capture.invocation_id, raw_digest,
                                               sha256(payload).hexdigest(), payload)
        return ArtifactReference(handle)

    def dispatch(self, reference: ArtifactReference, *, identity: TaskIdentity,
                 invocation: Invocation, request_id: str, sink: ArtifactSink) -> None:
        """Atomically consume one exact release; uncertain sends remain consumed.

        Both registry construction and sink implementation are host-only seams.
        This method does not enable ordinary LLM terminal-result authorization.
        """
        with self._artifact_lock:
            artifact = self._artifacts.get(reference.handle)
            if (artifact is None or not request_id or identity.turn in self._closed_turns
                    or request_id in self._closed_requests):
                raise ArtifactDenied("artifact_unknown")
            approval = artifact.approval
            if (identity != approval.identity or invocation_digest(invocation) != approval.invocation_digest
                    or sink.recipient != approval.recipient or request_id != approval.request_id):
                raise ArtifactDenied("release_binding_mismatch")
            if (self._authority is None or not self._authority.current(approval)
                    or self._clock() >= approval.expires_at):
                raise ArtifactDenied("approval_revoked_or_expired")
            if sha256(artifact.payload).hexdigest() != artifact.presentation_digest:
                raise ArtifactDenied("presentation_changed")
            validate_sanitized_text(artifact.payload.decode("utf-8"), max_bytes=32_768)
            task_key = (identity.node, identity.profile, identity.task)
            # Same task budgets cannot be reset by refreshing approval IDs/generations.
            count, size = self._budgets.get(task_key, (0, 0))
            request_key = (*task_key, request_id)
            if request_key in self._spent_requests:
                raise ArtifactDenied("request_already_consumed")
            if count + 1 > approval.request_budget or size + len(artifact.payload) > approval.export_budget:
                raise ArtifactDenied("task_budget_exceeded")
            with self._authority.dispatch_scope(approval, len(artifact.payload)):
                self._spent_requests.add(request_key)
                self._budgets[task_key] = (count + 1, size + len(artifact.payload))
                # A grant releases once, even if the sink raises after accepting bytes.
                del self._artifacts[reference.handle]
                sink.send(request_id, artifact.payload)

    def clear_request(self, request_id: str) -> None:
        """Close the artifact request, including capture currently in flight."""
        super().clear_request(request_id)
        with self._artifact_lock:
            self._closed_requests.add(request_id)
            self._artifacts = {key: value for key, value in self._artifacts.items()
                               if value.approval.request_id != request_id}

    def clear_turn(self, turn_id: str) -> None:
        """Discard authority and bytes; retain consumed budgets until registry dies."""
        super().clear_turn(turn_id)
        with self._artifact_lock:
            self._closed_turns.add(turn_id)
            self._artifacts = {key: value for key, value in self._artifacts.items()
                               if value.approval.identity.turn != turn_id}
