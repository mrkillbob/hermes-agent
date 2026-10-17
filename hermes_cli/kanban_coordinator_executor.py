"""Bounded local receipt handoff; submit/lookup never launch a worker or model."""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_external import Artifact, Delivery, artifact_from_envelope


@dataclass(frozen=True)
class CoordinatorLimits:
    submit_seconds: float = 2.0
    lookup_seconds: float = 1.0
    preflight_seconds: float = 10.0
    execution_seconds: float = 120.0
    terminate_grace_seconds: float = 3.0
    kill_grace_seconds: float = 2.0
    stream_bytes: int = 65536

    def __post_init__(self):
        caps = (2, 1, 10, 120, 3, 2, 65536)
        for (name, value), cap in zip(asdict(self).items(), caps):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= cap:
                raise ValueError('Coordinator limits must be finite, positive and within fixed caps')
            if name == 'stream_bytes' and type(value) is not int:
                raise ValueError('Stream cap must be an integer')


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def envelope(artifact: Artifact) -> dict:
    return {'artifact': asdict(artifact), 'sha256': artifact.sha256}


@contextmanager
def bounded_connection(database: Path, seconds: float, *, writable=False):
    """No retries/migrations; deadlines cover SQL work and bounded lock waits.

    A stalled kernel/filesystem is outside application-level real-time guarantees.
    """
    deadline = time.monotonic() + seconds
    if writable:
        kb._assert_not_delegated_child_mutation(database)
    uri = database.as_uri() + ('?mode=rw' if writable else '?mode=ro')
    with closing(sqlite3.connect(uri, uri=True, timeout=min(seconds, .2 if writable else .1), isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        try:
            if writable:
                conn.execute('PRAGMA synchronous=FULL')
                conn.execute('BEGIN IMMEDIATE')
            else:
                conn.execute('PRAGMA query_only=ON')
            yield conn
            if time.monotonic() >= deadline:
                raise TimeoutError('Coordinator SQL deadline')
            if writable:
                conn.commit()
        finally:
            conn.set_progress_handler(None, 0)
            if conn.in_transaction:
                conn.rollback()


def event(conn, delivery: Delivery, kind: str):
    row = conn.execute(
        'SELECT CASE WHEN length(CAST(payload AS BLOB))<=65536 THEN payload END AS payload, '
        'length(CAST(payload AS BLOB)) AS size FROM task_events '
        'WHERE task_id=? AND run_id=? AND kind=? ORDER BY id DESC LIMIT 1',
        (delivery.task_id, delivery.run_id, kind)).fetchone()
    if row is None:
        return None
    if row['size'] is None or row['size'] > 65536:
        raise ValueError('Coordinator event exceeds envelope limit')
    value = json.loads(row['payload'])
    if not isinstance(value, dict):
        raise ValueError('Malformed coordinator event')
    return value


def validate_binding(conn, delivery, database, coordinator_id, *, owned=False):
    if (not isinstance(delivery, Delivery) or delivery.database != str(database)
            or delivery.executor != coordinator_id):
        raise ValueError('Foreign coordinator binding')
    if event(conn, delivery, 'external_intent') != asdict(delivery):
        raise ValueError('Delivery differs from canonical intent')
    if owned:
        row = conn.execute('SELECT status,current_run_id,claim_lock,assignee,title,body FROM tasks WHERE id=?',
                           (delivery.task_id,)).fetchone()
        if (row is None or row['status'] != 'running' or row['current_run_id'] != delivery.run_id
                or row['claim_lock'] != delivery.claim or row['assignee'] != delivery.lane
                or row['title'] != delivery.title or (row['body'] or '') != delivery.body):
            raise ValueError('Coordinator task ownership changed')



class CoordinatorExecutor:
    def __init__(self, database: Path, coordinator_id: str, limits: CoordinatorLimits):
        self.database = Path(database).resolve(strict=True)
        if not isinstance(coordinator_id, str) or not coordinator_id.strip() or not isinstance(limits, CoordinatorLimits):
            raise ValueError('Explicit coordinator identity and limits required')
        self.coordinator_id, self.limits = coordinator_id, limits

    def submit(self, delivery: Delivery) -> Artifact | None:
        artifact = Artifact(delivery, 'accepted', 'Offer retained; coordinator has not acknowledged')
        try:
            with bounded_connection(self.database, self.limits.submit_seconds, writable=True) as conn:
                validate_binding(conn, delivery, self.database, self.coordinator_id, owned=True)
                old = event(conn, delivery, 'external_coordinator_offer')
                value = envelope(artifact)
                if old is not None and old != json.loads(json.dumps(value)):
                    raise ValueError('Conflicting coordinator offer')
                if old is None:
                    kb._append_event(conn, delivery.task_id, 'external_coordinator_offer', value, run_id=delivery.run_id)
            return artifact
        except (sqlite3.Error, TimeoutError):
            return None

    def lookup(self, delivery: Delivery) -> Artifact | None:
        try:
            with bounded_connection(self.database, self.limits.lookup_seconds) as conn:
                validate_binding(conn, delivery, self.database, self.coordinator_id)
                for kind in ('external_terminal', 'external_started', 'external_ack', 'external_coordinator_offer'):
                    value = event(conn, delivery, kind)
                    if value is not None:
                        artifact = artifact_from_envelope(value)
                        if artifact.delivery != delivery:
                            raise ValueError('Receipt belongs to another delivery')
                        return artifact
            return None
        except (sqlite3.Error, TimeoutError):
            return None

    def delivery(self, task_id: str, run_id: int, key: str) -> Delivery:
        with bounded_connection(self.database, self.limits.lookup_seconds) as conn:
            row = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND run_id=? "
                               "AND kind='external_intent' AND length(CAST(payload AS BLOB))<=65536 "
                               "ORDER BY id LIMIT 1", (task_id, run_id)).fetchone()
            if row is None:
                raise ValueError('No bounded external delivery')
            delivery = Delivery(**json.loads(row['payload']))
            validate_binding(conn, delivery, self.database, self.coordinator_id)
            if delivery.key != key:
                raise ValueError('Foreign delivery key')
            return delivery

    def record(self, delivery, kind, payload, *, exclusive=False) -> bool:
        value = {'binding': digest(asdict(delivery)), **payload}
        if len(json.dumps(value).encode()) > 65536:
            raise ValueError('Coordinator event exceeds envelope limit')
        with bounded_connection(self.database, self.limits.submit_seconds, writable=True) as conn:
            validate_binding(conn, delivery, self.database, self.coordinator_id)
            old = event(conn, delivery, kind)
            if old is not None:
                if not exclusive and old != value:
                    raise ValueError('Conflicting coordinator evidence')
                return False
            validate_binding(conn, delivery, self.database, self.coordinator_id, owned=True)
            kb._append_event(conn, delivery.task_id, kind, value, run_id=delivery.run_id)
            return True

    def read(self, delivery, kind):
        with bounded_connection(self.database, self.limits.lookup_seconds) as conn:
            validate_binding(conn, delivery, self.database, self.coordinator_id)
            value = event(conn, delivery, kind)
            if value is not None and value.get('binding') != digest(asdict(delivery)):
                raise ValueError('Foreign coordinator event binding')
            return value
