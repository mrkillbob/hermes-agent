"""Owner-side external lane boundary. No executors, endpoints or runtime registration.

A trusted caller supplies bounded submit/read-only lookup implementations. Correlation
hashes do not authenticate a peer or confer worker authority. Events share Kanban's
existing durable store; uncertain effects are never automatically submitted again.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Literal, Protocol
from uuid import uuid4

from hermes_cli import kanban_db as kb

ArtifactKind = Literal['accepted', 'ack', 'started', 'completed', 'question', 'blocked', 'error']
TERMINAL = frozenset({'completed', 'question', 'blocked', 'error'})
KINDS = TERMINAL | {'accepted', 'ack', 'started'}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Delivery:
    database: str
    task_id: str
    run_id: int
    claim: str
    lane: str
    executor: str
    key: str
    payload_sha256: str
    title: str
    body: str
    workspace: str

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == 'run_id':
                if type(value) is not int or value <= 0:
                    raise ValueError('Delivery run id must be a positive integer')
            elif not isinstance(value, str) or (name != 'body' and not value.strip()):
                raise ValueError('Delivery fields require explicit text')
        if self.payload_sha256 != _digest([self.title, self.body, self.workspace]):
            raise ValueError('Delivery payload integrity mismatch')
        if len(json.dumps(asdict(self), ensure_ascii=False).encode()) > 65536:
            raise ValueError('Delivery exceeds 64 KiB')


@dataclass(frozen=True)
class Artifact:
    delivery: Delivery
    kind: ArtifactKind
    summary: str
    findings: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.delivery, Delivery) or not isinstance(self.kind, str) or self.kind not in KINDS:
            raise ValueError('Invalid external artifact type')
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError('External artifact requires evidence or a reason')
        for values in (self.findings, self.questions):
            if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError('Artifact lists must be immutable nonblank text')
        if len(json.dumps(asdict(self), ensure_ascii=False).encode()) > 65536:
            raise ValueError('External artifact exceeds 64 KiB')

    @property
    def sha256(self) -> str:
        return _digest(asdict(self))


class Executor(Protocol):
    def submit(self, delivery: Delivery) -> Artifact | None: ...
    def lookup(self, delivery: Delivery) -> Artifact | None: ...


@dataclass(frozen=True)
class Lane:
    executor_id: str
    executor: Executor


def _database(conn: sqlite3.Connection) -> str:
    filename = conn.execute('PRAGMA database_list').fetchone()['file']
    if not filename:
        raise ValueError('External delivery requires a durable Kanban database')
    return str(Path(filename).resolve())


def _event(conn, task_id, run_id, kind):
    row = conn.execute('SELECT payload FROM task_events WHERE task_id=? AND run_id=? AND kind=? '
                       'ORDER BY id LIMIT 1', (task_id, run_id, kind)).fetchone()
    return json.loads(row['payload']) if row else None


def reclaim_paused(conn, task_id: str) -> bool:
    """Any current external intent fences automated PID/TTL-based recovery.

    Presence alone holds even malformed evidence. Neither age nor a missing registry
    proves that the executor stopped. Operator reclaim remains an explicit action.
    """
    return conn.execute(
        "SELECT 1 FROM task_events e JOIN tasks t ON t.current_run_id=e.run_id "
        "WHERE t.id=? AND e.task_id=t.id AND e.kind='external_intent' LIMIT 1",
        (task_id,)).fetchone() is not None


def artifact_from_envelope(payload: dict) -> Artifact:
    """Parse a closed JSON-compatible envelope; digest is integrity, not authority."""
    try:
        if not isinstance(payload, dict) or set(payload) != {'artifact', 'sha256'}:
            raise ValueError('Invalid external envelope fields')
        data = dict(payload['artifact'])
        data['delivery'] = Delivery(**data['delivery'])
        for key in ('findings', 'questions'):
            if not isinstance(data[key], (list, tuple)):
                raise ValueError('Artifact lists must be text arrays')
            data[key] = tuple(data[key])
        result = Artifact(**data)
        if result.sha256 != payload['sha256']:
            raise ValueError('Stored external artifact integrity mismatch')
        return result
    except (KeyError, TypeError) as exc:
        raise ValueError('Malformed external artifact') from exc


def _applied(conn, artifact: Artifact) -> bool:
    delivery = artifact.delivery
    run = kb.get_run(conn, delivery.run_id)
    if run is None or run.task_id != delivery.task_id:
        return False
    expected = 'completed' if artifact.kind == 'completed' else 'blocked'
    if run.outcome != expected or run.summary != artifact.summary:
        return False
    if expected == 'completed':
        return (run.metadata or {}).get('external_artifact_sha256') == artifact.sha256
    return any(e.kind == 'blocked' and e.run_id == delivery.run_id
               and (e.payload or {}).get('reason') == artifact.summary
               for e in kb.list_events(conn, delivery.task_id))


def unresolved_history(conn, task_id: str) -> bool:
    """Reclaiming an uncertain run never grants permission to repeat its effects."""
    rows = conn.execute("SELECT run_id FROM task_events WHERE task_id=? AND kind='external_intent'",
                        (task_id,)).fetchall()
    for row in rows:
        try:
            terminal = _event(conn, task_id, row['run_id'], 'external_terminal')
            if terminal is None or not _applied(conn, artifact_from_envelope(terminal)):
                return True
        except (ValueError, KeyError, TypeError):
            return True
    return False


def profile_spawnable(assignee, profile_exists):
    if profile_exists is None:
        return not assignee.startswith('external-')
    return bool(profile_exists(assignee))


def record_nonspawnable(conn, task_id, assignee, result, dry_run, *, held=False):
    """Retain the dispatcher's once-per-change missing-profile diagnostic."""
    result.skipped_nonspawnable.append(task_id)
    if dry_run:
        return
    kind = 'external_held' if held else 'skipped_nonspawnable'
    payload = {'assignee': assignee}
    with kb.write_txn(conn):
        last = conn.execute(
            "SELECT kind, payload FROM task_events WHERE task_id=? "
            "ORDER BY created_at DESC, id DESC LIMIT 1", (task_id,)).fetchone()
        if (last is None or last['kind'] != kind
                or last['payload'] != kb._json_or_null(payload)):
            kb._append_event(conn, task_id, kind, payload)


def _owns(current, delivery):
    return (current is not None and current.status == 'running'
            and current.current_run_id == delivery.run_id and current.claim_lock == delivery.claim
            and current.assignee == delivery.lane and current.title == delivery.title
            and (current.body or '') == delivery.body)


def call_profile_spawn(spawn_fn, task, workspace, board):
    """Back-compat: older spawn_fn signatures (and test stubs) accept only
    ``(task, workspace)``; pass ``board`` only when the callable supports it."""
    import inspect
    try:
        sig = inspect.signature(spawn_fn)
        if "board" in sig.parameters:
            return spawn_fn(task, workspace, board=board)
        return spawn_fn(task, workspace)
    except (TypeError, ValueError):
        return spawn_fn(task, workspace)


def has_external_intent(conn, external, task):
    return external and conn.execute(
        "SELECT 1 FROM task_events WHERE task_id=? AND run_id=? AND kind='external_intent'",
        (task.id, task.current_run_id),
    ).fetchone() is not None


class ExternalLanes:
    """Explicit process-local registry carried through the existing spawn seam.

    No global registry, discovery, task-provided executable, environment or callback.
    External review workers are deliberately unsupported in this minimal boundary.
    """
    def __init__(self, lanes: dict[str, Lane], *, profile_spawn=None):
        self._lanes = dict(lanes)
        self._profile_spawn = profile_spawn
        for name, lane in self._lanes.items():
            if re.fullmatch(r'external-[a-z0-9][a-z0-9_-]*', name) is None:
                raise ValueError('External lane requires a canonical external- name')
            if not isinstance(lane, Lane) or not isinstance(lane.executor_id, str) or not lane.executor_id.strip():
                raise ValueError('Explicit executor identity required')
            if not callable(getattr(lane.executor, 'submit', None)) or not callable(getattr(lane.executor, 'lookup', None)):
                raise ValueError('Executor requires submit and read-only lookup')

    def registered(self, name: str) -> bool:
        from hermes_cli.profiles import profile_exists
        return name in self._lanes and not profile_exists(name)

    def __call__(self, task, workspace, board=None):
        from hermes_cli import kanban_db_dispatch as dispatch
        return call_profile_spawn(self._profile_spawn or dispatch._default_spawn, task, workspace, board)

    def _binding(self, conn, delivery: Delivery):
        if not isinstance(delivery, Delivery) or delivery.database != _database(conn):
            raise ValueError('Foreign external board binding')
        lane = self._lanes.get(delivery.lane)
        if lane is None or lane.executor_id != delivery.executor:
            raise ValueError('Unregistered external executor binding')
        stored = _event(conn, delivery.task_id, delivery.run_id, 'external_intent')
        if stored != asdict(delivery):
            raise ValueError('External task/run/claim/payload binding mismatch')
        return lane

    def deliver(self, conn, task, workspace: str) -> Delivery:
        lane = self._lanes.get(task.assignee)
        if lane is None or not self.registered(task.assignee):
            raise ValueError('Unregistered external lane')
        with kb.write_txn(conn):
            current = kb.get_task(conn, task.id)
            if (current is None or current.status != 'running' or current.current_run_id != task.current_run_id
                    or not current.claim_lock or current.claim_lock != task.claim_lock or current.assignee != task.assignee):
                raise ValueError('External delivery requires the current owned run')
            previous = _event(conn, task.id, task.current_run_id, 'external_intent')
            if previous is not None:
                delivery = Delivery(**previous)
                self._binding(conn, delivery)
                if (delivery.claim != current.claim_lock or delivery.title != current.title
                        or delivery.body != (current.body or '') or delivery.workspace != workspace):
                    raise ValueError('External delivery input changed')
                return delivery
            if unresolved_history(conn, task.id):
                raise ValueError('Earlier external effects require reconciliation')
            delivery = Delivery(_database(conn), task.id, task.current_run_id, task.claim_lock,
                                task.assignee, lane.executor_id, uuid4().hex,
                                _digest([task.title, task.body or '', workspace]), task.title, task.body or '', workspace)
            kb._append_event(conn, task.id, 'external_intent', asdict(delivery), run_id=task.current_run_id)
        self._submit(conn, delivery, lane)
        return delivery

    def _submit(self, conn, delivery, lane):
        # Never hold a database transaction through executor effects. Configured
        # dispatch defers this hook until both admission locks have been released.
        try:
            receipt = lane.executor.submit(delivery)
        except Exception as exc:  # health: allow BLE001 -- untrusted transport errors may contain secrets; retain only class and uncertainty, never retry
            with kb.write_txn(conn):
                kb._append_event(conn, delivery.task_id, 'external_uncertain', {'error_class': type(exc).__name__}, run_id=delivery.run_id)
            return delivery
        if receipt is not None:
            self._receive_for(conn, delivery, receipt)
        return delivery

    def receive(self, conn, artifact: Artifact) -> bool:
        if not isinstance(artifact, Artifact):
            raise ValueError('Typed external artifact required')
        delivery = artifact.delivery
        terminal = artifact.kind in TERMINAL
        with kb.write_txn(conn):
            self._binding(conn, delivery)
            previous = _event(conn, delivery.task_id, delivery.run_id, 'external_terminal')
            if previous is not None:
                if terminal and artifact_from_envelope(previous) != artifact:
                    raise ValueError('Conflicting external terminal artifact')
                return_artifact = artifact_from_envelope(previous)
            else:
                current = kb.get_task(conn, delivery.task_id)
                if not _owns(current, delivery):
                    raise ValueError('External run ownership changed')
                payload = {'artifact': asdict(artifact), 'sha256': artifact.sha256}
                kind = 'external_terminal' if terminal else 'external_' + artifact.kind
                old = _event(conn, delivery.task_id, delivery.run_id, kind)
                if old is not None and artifact_from_envelope(old) != artifact:
                    raise ValueError('Conflicting external observation')
                if old is None:
                    kb._append_event(conn, delivery.task_id, kind, payload, run_id=delivery.run_id)
                return_artifact = artifact
        if not terminal and previous is None:
            return False
        return self._apply(conn, return_artifact)

    def _apply(self, conn, artifact: Artifact) -> bool:
        if _applied(conn, artifact):
            return True
        delivery = artifact.delivery
        current = kb.get_task(conn, delivery.task_id)
        if not _owns(current, delivery):
            raise ValueError('External run was superseded before terminal application')
        if artifact.kind == 'completed':
            return kb.complete_task(conn, delivery.task_id, result=artifact.summary,
                                    summary=artifact.summary, expected_run_id=delivery.run_id,
                                    metadata={'external_artifact_sha256': artifact.sha256})
        return kb.block_task(conn, delivery.task_id, reason=artifact.summary,
                             kind='needs_input', expected_run_id=delivery.run_id)

    def reconcile(self, conn, delivery: Delivery) -> bool:
        lane = self._binding(conn, delivery)
        previous = _event(conn, delivery.task_id, delivery.run_id, 'external_terminal')
        if previous is not None:
            return self.receive(conn, artifact_from_envelope(previous))
        receipt = lane.executor.lookup(delivery)
        return self._receive_for(conn, delivery, receipt) if receipt is not None else False

    def _receive_for(self, conn, delivery, receipt):
        if not isinstance(receipt, Artifact) or receipt.delivery != delivery:
            raise ValueError('Executor returned another delivery result')
        return self.receive(conn, receipt)
