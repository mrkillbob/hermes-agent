"""Inactive, transport-only peer mail. No model, task database or approval access.

Controller-owned routes fence a session incarnation. Accepted means a recipient
accepted a message for handling, never accepted work or human permission. The
caller must authenticate evidence; raw peer payloads are not receipts.
"""
import base64
import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, fields

KINDS = frozenset({"message", "task_request", "task_result", "failure", "needs_input",
                   "input_response", "cancel_request", "cancel_result"})
STAGES = frozenset({"queued", "recipient_stored", "processed", "accepted"})
SCHEMA = "peer.message"
MAX_WIRE = 96 * 1024


def _unique_record(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransportError("duplicate field")
        result[key] = value
    return result


class TransportError(ValueError):
    """Delivery held because identity, compatibility or evidence is invalid."""


@dataclass(frozen=True)
class Envelope:
    message_id: str
    sender: str
    recipient: str
    conversation_id: str
    correlation_id: str
    kind: str
    payload: bytes
    created_at: int
    expires_at: int
    in_reply_to: str | None = None
    generation: str | None = None
    version: str = "1.0"

    def encode(self) -> bytes:
        texts = (self.message_id, self.sender, self.recipient, self.conversation_id,
                 self.correlation_id)
        if any(not isinstance(v, str) or not v or len(v) > 256 for v in texts):
            raise TransportError("invalid identity")
        if self.version != "1.0" or self.kind not in KINDS:
            raise TransportError("unsupported version or kind")
        if type(self.created_at) is not int or type(self.expires_at) is not int:
            raise TransportError("invalid time")
        if self.created_at < 0 or self.expires_at <= self.created_at:
            raise TransportError("invalid expiry")
        for value in (self.in_reply_to, self.generation):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
                raise TransportError("invalid reference")
        if self.kind in {"needs_input", "input_response", "cancel_request", "cancel_result"}:
            if not self.generation:
                raise TransportError("request generation required")
        if not isinstance(self.payload, bytes) or len(self.payload) > 65536:
            raise TransportError("payload exceeds bound")
        record = asdict(self)
        record["schema"] = SCHEMA
        record["payload"] = base64.b64encode(self.payload).decode("ascii")
        wire = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        if len(wire) > MAX_WIRE:
            raise TransportError("wire exceeds bound")
        return wire

    @classmethod
    def decode(cls, wire: bytes):
        if len(wire) > MAX_WIRE:
            raise TransportError("wire exceeds bound")
        try:
            record = json.loads(wire, object_pairs_hook=_unique_record)
            if record.pop("schema") != SCHEMA:
                raise TransportError("unsupported schema")
            if set(record) != {field.name for field in fields(cls)}:
                raise TransportError("unsupported or missing envelope fields")
            record["payload"] = base64.b64decode(record["payload"], validate=True)
            envelope = cls(**record)
            envelope.encode()
            return envelope
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise TransportError("invalid envelope") from exc

    @property
    def digest(self):
        return hashlib.sha256(self.encode()).hexdigest()


@dataclass(frozen=True)
class Route:
    peer: str
    binding: str
    epoch: int
    conversation_id: str

    def validate(self):
        if (any(not isinstance(v, str) or not v or len(v) > 256
                for v in (self.peer, self.binding, self.conversation_id))
                or type(self.epoch) is not int or self.epoch < 1):
            raise TransportError("invalid controller route")


@dataclass(frozen=True)
class Delivery:
    envelope: Envelope
    route: Route
    token: str


@dataclass(frozen=True)
class Acknowledgement:
    message_id: str
    digest: str
    route: Route
    stage: str
    version: str = "1.0"

    def encode(self):
        self.route.validate()
        if self.version != "1.0" or self.stage not in STAGES:
            raise TransportError("unsupported acknowledgement")
        if (not isinstance(self.message_id, str) or not self.message_id
                or len(self.message_id) > 256 or not isinstance(self.digest, str)
                or len(self.digest) != 64 or any(c not in "0123456789abcdef" for c in self.digest)):
            raise TransportError("invalid acknowledgement identity")
        return json.dumps({"schema": "peer.ack", **asdict(self)},
                          sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def decode(cls, wire):
        if len(wire) > 4096:
            raise TransportError("acknowledgement exceeds bound")
        try:
            record = json.loads(wire, object_pairs_hook=_unique_record)
            if record.pop("schema") != "peer.ack":
                raise TransportError("unsupported acknowledgement schema")
            if set(record) != {field.name for field in fields(cls)}:
                raise TransportError("unsupported or missing acknowledgement fields")
            record["route"] = Route(**record["route"])
            ack = cls(**record)
            ack.encode()
            return ack
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise TransportError("invalid acknowledgement") from exc


class Mailbox:
    """Caller supplies an isolated path and authenticated routes; nothing starts itself.

    Claims commit before I/O. A lost lease replays identical sender mail. Receiver
    handling is at most once: a crash after claiming leaves uncertain work visible
    as `handling`, for owner reconciliation rather than a second model invocation.
    Rows are retained; deleting dedup records requires a separate retention policy.
    """

    def __init__(self, path, *, max_attempts=3):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise TransportError("invalid attempt bound")
        self.max_attempts = max_attempts
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS peer_mail (
            direction TEXT, id TEXT, wire BLOB, digest TEXT, peer TEXT,
            binding TEXT, epoch INTEGER, attempts INTEGER DEFAULT 0,
            lease_until INTEGER DEFAULT 0, token TEXT, disposition TEXT DEFAULT 'pending',
            sender_stored INTEGER DEFAULT 0, queued INTEGER DEFAULT 0,
            recipient_stored INTEGER DEFAULT 0, processed INTEGER DEFAULT 0,
            accepted INTEGER DEFAULT 0, PRIMARY KEY(direction, id))""")

    def close(self):
        self.db.close()

    def _insert(self, envelope, route, direction):
        wire = envelope.encode()
        route.validate()
        if envelope.recipient != route.peer or envelope.conversation_id != route.conversation_id:
            raise TransportError("recipient route mismatch")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM peer_mail WHERE direction=? AND id=?",
                                  (direction, envelope.message_id)).fetchone()
            if row:
                if (row["wire"] != wire or row["binding"] != route.binding
                        or row["epoch"] != route.epoch):
                    raise TransportError("conflicting delivery identity")
                added = False
            else:
                self.db.execute("""INSERT INTO peer_mail
                    (direction,id,wire,digest,peer,binding,epoch,sender_stored,recipient_stored)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (direction, envelope.message_id, wire,
                    envelope.digest, route.peer, route.binding, route.epoch,
                    int(direction == "out"), int(direction == "in")))
                added = True
            self.db.execute("COMMIT")
            return added
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def store(self, envelope, route):
        return self._insert(envelope, route, "out")

    def receive(self, envelope, route, *, authenticated_sender, now):
        if authenticated_sender != envelope.sender:
            raise TransportError("sender provenance mismatch")
        if not envelope.created_at <= now < envelope.expires_at:
            raise TransportError("expired or future mail")
        return self._insert(envelope, route, "in")

    def claim(self, route, *, now, lease_seconds=30):
        route.validate()
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise TransportError("invalid lease")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            delivery = self._claim(route, now, lease_seconds)
            self.db.execute("COMMIT")
            return delivery
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _claim(self, route, now, lease_seconds):
        rows = self.db.execute("""SELECT * FROM peer_mail WHERE direction='out'
            AND peer=? AND disposition='pending' ORDER BY rowid""", (route.peer,)).fetchall()
        for row in rows:
            envelope = Envelope.decode(row["wire"])
            if envelope.conversation_id != route.conversation_id:
                continue
            reason = None
            if row["binding"] != route.binding or row["epoch"] != route.epoch:
                reason = "route_changed"
            elif now >= envelope.expires_at:
                reason = "expired"
            elif row["lease_until"] > now or now < envelope.created_at:
                continue
            elif row["attempts"] >= self.max_attempts:
                reason = "retry_exhausted"
            if reason:
                self.db.execute("UPDATE peer_mail SET disposition=? WHERE direction='out' AND id=?",
                                (reason, row["id"]))
                continue
            token = str(uuid.uuid4())
            self.db.execute("""UPDATE peer_mail SET attempts=attempts+1,token=?,lease_until=?
                WHERE direction='out' AND id=?""", (token, min(now + lease_seconds,
                                                             envelope.expires_at), row["id"]))
            return Delivery(envelope, route, token)
        return None

    def evidence(self, delivery, stage, *, now):
        """Trusted adapter evidence only. Stages are independent facts, not a ladder."""
        if stage not in STAGES:
            raise TransportError("unsupported evidence stage")
        if delivery.route.conversation_id != delivery.envelope.conversation_id:
            raise TransportError("evidence conversation mismatch")
        status = "pending" if stage == "queued" else "delivered"
        changed = self.db.execute(f"""UPDATE peer_mail SET {stage}=1,
            disposition=CASE WHEN disposition='delivered' THEN disposition ELSE ? END
            WHERE direction='out' AND id=? AND digest=? AND peer=? AND binding=?
            AND epoch=? AND token=? AND disposition IN ('pending','delivered')""",
            (status, delivery.envelope.message_id, delivery.envelope.digest, delivery.route.peer,
             delivery.route.binding, delivery.route.epoch, delivery.token)).rowcount
        if changed != 1:
            raise TransportError("stale or mismatched delivery evidence")

    def acknowledge(self, delivery, ack, *, authenticated_peer, now):
        ack.encode()
        if (authenticated_peer != delivery.route.peer or ack.route != delivery.route
                or ack.message_id != delivery.envelope.message_id
                or ack.digest != delivery.envelope.digest):
            raise TransportError("acknowledgement provenance mismatch")
        self.evidence(delivery, ack.stage, now=now)

    def begin_handling(self, message_id, route, *, now):
        route.validate()
        row = self.db.execute("SELECT * FROM peer_mail WHERE direction='in' AND id=?",
                              (message_id,)).fetchone()
        if row is None:
            raise TransportError("unknown recipient message")
        envelope = Envelope.decode(row["wire"])
        if (row["peer"] != route.peer or row["binding"] != route.binding
                or row["epoch"] != route.epoch or envelope.conversation_id != route.conversation_id):
            return False
        if not envelope.created_at <= now < envelope.expires_at:
            self.db.execute("""UPDATE peer_mail SET disposition='expired'
                WHERE direction='in' AND id=? AND disposition='pending'""", (message_id,))
            return False
        return self.db.execute("""UPDATE peer_mail SET disposition='handling'
            WHERE direction='in' AND id=? AND disposition='pending'""", (message_id,)).rowcount == 1

    def finish_handling(self, message_id, route, *, stage):
        """Receiver-owned disposition, persisted before returning a receipt."""
        route.validate()
        if stage not in {"processed", "accepted"}:
            raise TransportError("unsupported handling fact")
        row = self.db.execute("SELECT * FROM peer_mail WHERE direction='in' AND id=?",
                              (message_id,)).fetchone()
        if row is None:
            raise TransportError("unknown recipient message")
        envelope = Envelope.decode(row["wire"])
        if envelope.conversation_id != route.conversation_id:
            raise TransportError("handling conversation mismatch")
        changed = self.db.execute(f"""UPDATE peer_mail SET {stage}=1,disposition='handled'
            WHERE direction='in' AND id=? AND peer=? AND binding=? AND epoch=?
            AND disposition IN ('handling','handled')""",
            (message_id, route.peer, route.binding, route.epoch)).rowcount
        if changed != 1:
            raise TransportError("handling route or claim mismatch")
        return Acknowledgement(message_id, row["digest"], route, stage)

    def status(self, message_id, *, direction="out"):
        row = self.db.execute("SELECT * FROM peer_mail WHERE direction=? AND id=?",
                              (direction, message_id)).fetchone()
        if row is None:
            raise TransportError("unknown message")
        result = dict(row)
        result["payload"] = Envelope.decode(result.pop("wire")).payload
        return result
