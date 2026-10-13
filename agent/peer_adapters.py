"""Inactive adapters for controller-supplied pipes and authorized queue callbacks.

No process launch, session discovery, credentials, permissions, forwarding or
model calls. Pipe ownership and callback authorization are host responsibilities.
"""
import json
from dataclasses import dataclass
from threading import Lock

from agent.peer_transport import Delivery, Route, TransportError


@dataclass(frozen=True)
class Observation:
    delivery: Delivery
    stage: str
    raw: bytes
    outcome: str | None = None


class OwnedStream:
    """One outstanding input on an adapter-owned stream-JSON process.

    The host keeps stdin open and drains stdout. Input echoes prove admission
    only. A session-matching result proves handling for the sole outstanding
    input, never receipt persistence, accepted work or confirmed cancellation.
    A lost stream must be reconciled before reusing this session.

    Durable controllers must bind their mailbox and use deliver_next, or send a
    current claim through the bound adapter. Before pipe I/O it parks the claim;
    this surface has no recipient-side durable dedup, so uncertainty never
    becomes automatic replay. Raw unbound send is framing-only qualification.
    """

    def __init__(self, stdin, route: Route, *, mailbox=None):
        route.validate()
        self.stdin = stdin
        self.route = route
        self.mailbox = mailbox
        self.active = None
        self.results = set()
        self.admitted = set()
        self.lock = Lock()

    def send(self, delivery: Delivery):
        with self.lock:
            return self._send(delivery)

    def deliver_next(self, *, now, lease_seconds=30):
        """Backpressure before claim; mailbox reservation before non-idempotent I/O."""
        if self.mailbox is None:
            raise TransportError("durable stream requires a bound mailbox")
        with self.lock:
            if self.active is not None:
                return None
            if len(self.admitted) >= 1024:
                raise TransportError("stream replay bound reached; reconcile session")
            delivery = self.mailbox.claim_stream(self.route, now=now, lease_seconds=lease_seconds)
            if delivery is not None:
                self._send(delivery)
            return delivery

    def _send(self, delivery):
        if delivery.route != self.route or delivery.envelope.recipient != self.route.peer:
            raise TransportError("stream route mismatch")
        if delivery.envelope.conversation_id != self.route.conversation_id:
            raise TransportError("stream conversation mismatch")
        if delivery.envelope.message_id in self.admitted:
            raise TransportError("stream input already admitted; reconcile receipt")
        if self.active is not None or len(self.admitted) >= 1024:
            if self.mailbox is not None:
                self.mailbox.defer(delivery)
            raise TransportError("stream busy or replay bound reached; hold in mailbox")
        frame = {"type": "user", "session_id": self.route.binding,
                 "uuid": delivery.envelope.message_id,
                 "message": {"role": "user", "content": delivery.envelope.encode().decode()}}
        wire = json.dumps(frame, separators=(",", ":")) + "\n"
        if self.mailbox is not None:
            self.mailbox.park(delivery)
        self.active = delivery
        self.admitted.add(delivery.envelope.message_id)
        written = self.stdin.write(wire)
        if written != len(wire):
            raise TransportError("partial stream write; reconcile owned session")
        self.stdin.flush()
        return "queued"

    def observe(self, raw: bytes):
        if len(raw) > 1024 * 1024:
            raise TransportError("stream event exceeds bound")
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise TransportError("invalid stream event") from exc
        if not isinstance(event, dict):
            raise TransportError("invalid stream event")
        with self.lock:
            delivery = self.active
            if delivery is None or event.get("session_id") != self.route.binding:
                return None
            if event.get("type") == "user" and event.get("uuid") == delivery.envelope.message_id:
                return Observation(delivery, "queued", raw)
            if event.get("type") == "result":
                identity = event.get("uuid")
                if (not isinstance(identity, str) or not identity or len(identity) > 256
                        or type(event.get("is_error")) is not bool):
                    raise TransportError("invalid stream result identity")
                if identity in self.results:
                    return None
                self.results.add(identity)
                self.active = None
                outcome = "failed" if event.get("is_error") is True else "completed"
                return Observation(delivery, "processed", raw, outcome)
            return None


class QueueEndpoint:
    """Explicitly bound host callback; a true return proves only queue admission.

    Hermes gateway/TUI callbacks must retain plugin/profile consent and exact
    durable session binding. Classic busy CLI injection interrupts and therefore
    is unsuitable here. Mira helper messaging needs its supported relationship;
    supplying a callback does not establish arbitrary external Mira ingress.
    """

    def __init__(self, route: Route, enqueue):
        route.validate()
        self.route = route
        self.enqueue = enqueue
        self.lock = Lock()

    def send(self, delivery: Delivery):
        if delivery.route != self.route or delivery.envelope.recipient != self.route.peer:
            raise TransportError("queue route mismatch")
        if delivery.envelope.conversation_id != self.route.conversation_id:
            raise TransportError("queue conversation mismatch")
        with self.lock:
            if self.enqueue(delivery.envelope.encode(), self.route.binding) is not True:
                raise TransportError("queue admission rejected")
        return "queued"
