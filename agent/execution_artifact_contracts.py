"""Host-only execution sharing contracts; no config, tool or wire activation surface.

Adapters are trusted host code, not model-provided attestations. No production
approval, confinement or delivery adapter is supplied by this development phase.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ContextManager
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json


class ArtifactDenied(ValueError):
    """Content-free refusal. Never include captured output in exception text."""


@dataclass(frozen=True, slots=True)
class TaskIdentity:
    task: str
    admission: str
    node: str
    profile: str
    session: str
    turn: str
    policy: str


@dataclass(frozen=True, slots=True)
class Recipient:
    """Separate model and owner-channel audiences; no implicit onward sharing."""
    kind: str
    account: str
    endpoint: str
    model: str


@dataclass(frozen=True, slots=True)
class Invocation:
    """Exact host-approved argv, never a shell string or ambient environment."""
    executable: str
    executable_digest: str
    argv: tuple[str, ...] = field(repr=False)
    cwd_identity: str
    project_identity: str
    operation: str


@dataclass(frozen=True, slots=True)
class Approval:
    """Authenticated host admission decision, not input accepted from a worker."""
    identity: TaskIdentity
    invocation_digest: str
    recipient: Recipient
    approval_id: str
    request_id: str
    invocation_id: str
    generation: int
    expires_at: float
    result_kind: str
    export_budget: int = 1_048_576
    request_budget: int = 10
    execution_plan_digest: str = ""


@dataclass(frozen=True, slots=True)
class Capture:
    """Returned exclusively by a trusted confined executor after settlement.

    The adapter must establish launch identity and descendant/input/network
    confinement, capture both streams directly with a combined 1 MiB ceiling,
    and drop raw-buffer references on every exit. Immutable returned bytes cannot
    promise secure erasure; callers must not persist traceback locals or raw reprs. Hashes alone never authorize data.
    """
    invocation_id: str
    identity: TaskIdentity
    invocation: Invocation
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    exit_code: int
    settled: bool
    confined: bool
    overflow: bool = False


class ApprovalAuthority(ABC):
    """Local authenticated approval boundary, unavailable until activation review."""
    @abstractmethod
    def approve(self, identity: TaskIdentity, invocation: Invocation, recipient: Recipient, request_id: str) -> Approval:
        """Verify approval/roots/argv/effects/route and atomically reserve invocation.

        Generate a unique invocation_id; reserve it durably before execution,
        including across registry instances. Never reissue an uncertain invocation.
        """

    @abstractmethod
    def current(self, approval: Approval) -> bool:
        """Recheck authenticated task/admission and revocation generation."""

    @abstractmethod
    def dispatch_scope(self, approval: Approval, byte_count: int) -> ContextManager[None]:
        """Authenticate, reserve and consume release under the admission lock.

        Must recheck identity/expiry/revocation, prevent request replay and enforce
        cumulative per-task budgets across admission changes, registry instances and restarts. Hold the lock
        through the single send; failures stay consumed. This is the dispatch
        linearization point; subsequent revocation cannot unsend bytes in flight.
        """


class ConfinedExecutor(ABC):
    """No local-backend fallback. A missing confinement adapter means refusal."""
    @abstractmethod
    def capture(self, approval: Approval, invocation: Invocation) -> Capture:
        """Run only the approved invocation with enforceable confinement.

        Must constrain symlinks/reparse points, descendants, inherited handles,
        filesystem inputs and network; no ambient secrets. Deny unsupported
        hosts, unknown ownership, capture loss or uncertain settlement.
        """


class ArtifactSink(ABC):
    """A trusted transport fixed to one authenticated recipient, without redirects."""
    @property
    @abstractmethod
    def recipient(self) -> Recipient: ...

    @abstractmethod
    def send(self, request_id: str, payload: bytes) -> None:
        """Send once without redirects/fallback/retry; error is uncertain delivery.

        Return or raise only after no further writes are possible. An async
        future timeout is not settlement; unqualified transports must refuse.
        Place bytes exclusively as untrusted tool DATA, never instructions. Verify
        authenticated recipient and exact final wire bytes at the transport fence;
        provider adapters must run the existing full request/firewall checks too.
        """


def invocation_digest(invocation: Invocation) -> str:
    """Bind every exact structured invocation field without retaining argv."""
    return sha256(json.dumps(asdict(invocation), sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()
