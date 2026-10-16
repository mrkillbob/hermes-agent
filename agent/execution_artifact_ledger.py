"""Disabled host approval ledger for execution artifacts; stores no result bytes.

This implements durable accounting, not an authenticated approval UI or transport.
Only a separately verified host issuer may call record_approval. No runtime caller
or activation setting is provided. Windows ACL qualification remains unavailable.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time

from agent.cross_process_file_lock import exclusive_file_lock
from agent.execution_artifact_fence import TaskFences, task_key
from agent.execution_artifact_contracts import (
    Approval, ApprovalAuthority, ArtifactDenied, Invocation, Recipient, TaskIdentity,
    invocation_digest,
)


def _body(approval: Approval) -> str:
    return json.dumps(asdict(approval), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _load(body: str) -> Approval:
    value = json.loads(body)
    value["identity"] = TaskIdentity(**value["identity"])
    value["recipient"] = Recipient(**value["recipient"])
    return Approval(**value)


def _task_key(identity: TaskIdentity) -> str:
    return task_key(identity)


def _private_file(path: Path) -> None:
    if os.name == "nt":
        raise ArtifactDenied("ledger_windows_acl_unqualified")
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        raise ArtifactDenied("ledger_owner_unqualified")
    parent = path.parent
    if parent.resolve(strict=True) != parent or not parent.is_dir():
        raise ArtifactDenied("ledger_directory_unverified")
    info = parent.stat()
    if info.st_uid != getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ArtifactDenied("ledger_directory_not_private")
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ArtifactDenied("ledger_file_not_private")
    finally:
        os.close(fd)


class DurableArtifactAuthority(ApprovalAuthority):
    """Private SQLite state plus a cross-process dispatch/revocation fence.

    Reservation commits before capture/send. Crash or uncertain send retains the
    consumed state. Reopening cannot reset invocation or per-task release budgets.
    Storage is trusted host-owned state, not tamper/rollback-resistant against
    the same OS user. Live admission changes must use this same dispatch fence
    (including per-record revoke) before changing admission/policy eligibility.
    A callback alone cannot serialize independently changing host state.
    The ledger authenticates previously recorded host decisions; it does not make
    a task body, approval ID or session ID into an authorized decision.
    """
    def __init__(self, path: Path, *, enabled=False, admission_check=None, clock=time.time):
        if enabled is not True:
            raise ArtifactDenied("execution_artifacts_disabled")
        if not callable(admission_check):
            raise ArtifactDenied("live_admission_verifier_missing")
        self._admission_check = admission_check
        self._path = Path(path).absolute()
        self._lock_path = self._path.with_suffix(".dispatch.lock")
        self._clock = clock
        self._quarantined: set[str] = set()
        self._fences = TaskFences(self._path)
        _private_file(self._path)
        with self._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, body TEXT NOT NULL, "
                       "state TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS budgets (task TEXT PRIMARY KEY, count INTEGER NOT NULL, "
                       "bytes INTEGER NOT NULL, max_count INTEGER NOT NULL, max_bytes INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS execution_requests "
                       "(task TEXT NOT NULL, request TEXT NOT NULL, PRIMARY KEY(task,request))")
            db.execute("CREATE TABLE IF NOT EXISTS invocations (id TEXT PRIMARY KEY, approval TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS quarantine (task TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE IF NOT EXISTS releases (task TEXT NOT NULL, request TEXT NOT NULL, "
                       "PRIMARY KEY(task, request))")

    @contextmanager
    def _transaction(self):
        _private_file(self._path)
        _private_file(self._lock_path)
        with exclusive_file_lock(self._lock_path):
            db = sqlite3.connect(self._path, timeout=5)
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def record_approval(self, approval: Approval) -> None:
        """Record an immutable, authenticated HOST decision. Not a wire endpoint.

        Caller must already verify owner/approval provenance, command schemas,
        project roots, destination/account and invocation effect permissions.
        This method must never be exposed to a model, task body or child process.
        """
        now = self._clock()
        if (not isinstance(approval, Approval) or not approval.approval_id
                or not approval.request_id or not approval.invocation_id
                or not math.isfinite(approval.expires_at)
                or not now < approval.expires_at <= now + 86_400
                or type(approval.export_budget) is not int or not 0 < approval.export_budget <= 1_048_576
                or type(approval.request_budget) is not int or not 0 < approval.request_budget <= 10):
            raise ArtifactDenied("ledger_approval_invalid")
        body = _body(approval)
        self._fences.check_entry()
        with self._fences.scope(approval.identity), self._transaction() as db:
            self._require_usable(db, approval.identity)
            row = db.execute("SELECT body FROM approvals WHERE id=?", (approval.approval_id,)).fetchone()
            if row is not None and row[0] != body:
                raise ArtifactDenied("ledger_approval_immutable")
            db.execute("INSERT OR IGNORE INTO approvals(id,body,state) VALUES(?,?,'approved')",
                       (approval.approval_id, body))
            task = _task_key(approval.identity)
            db.execute("INSERT OR IGNORE INTO budgets VALUES(?,0,0,?,?)",
                       (task, approval.request_budget, approval.export_budget))
            db.execute("UPDATE budgets SET max_count=MIN(max_count,?), max_bytes=MIN(max_bytes,?) WHERE task=?",
                       (approval.request_budget, approval.export_budget, task))

    def approve(self, identity: TaskIdentity, invocation: Invocation, recipient: Recipient,
                request_id: str) -> Approval:
        self._fences.check_entry()
        with self._fences.scope(identity):
            with self._transaction() as db:
                self._require_usable(db, identity)
                candidates = db.execute("SELECT body FROM approvals WHERE state='approved' AND revoked=0").fetchall()
            matches = [a for row in candidates if (a := _load(row[0])).identity == identity
                       and a.invocation_digest == invocation_digest(invocation)
                       and a.recipient == recipient and a.request_id == request_id
                       and a.expires_at > self._clock()]
            if len(matches) != 1:
                raise ArtifactDenied("ledger_approval_absent_or_ambiguous")
            approval = matches[0]
            if not self._admission_current(approval):
                raise ArtifactDenied("live_admission_changed")
            with self._transaction() as db:
                try:
                    db.execute("INSERT INTO invocations VALUES(?,?)", (approval.invocation_id, approval.approval_id))
                    db.execute("INSERT INTO execution_requests VALUES(?,?)", (_task_key(identity), request_id))
                except sqlite3.IntegrityError:
                    raise ArtifactDenied("ledger_execution_or_request_spent") from None
                db.execute("UPDATE approvals SET state='executing' WHERE id=?", (approval.approval_id,))
            return approval

    def _admission_current(self, approval) -> bool:
        try:
            return self._admission_check(approval) is True
        except (OSError, ValueError, RuntimeError):
            raise ArtifactDenied("admission_verification_unavailable") from None

    def _current(self, db, approval) -> bool:
        row = db.execute("SELECT body,state,revoked FROM approvals WHERE id=?", (approval.approval_id,)).fetchone()
        return bool(row and row == (_body(approval), "executing", 0) and approval.expires_at > self._clock())

    def _require_usable(self, db, identity):
        task = _task_key(identity)
        if task in self._quarantined or db.execute("SELECT 1 FROM quarantine WHERE task=?", (task,)).fetchone():
            raise ArtifactDenied("artifact_task_quarantined")

    def quarantine(self, identity: TaskIdentity) -> None:
        """Permanent fail-closed tombstone; no automatic expiry/reconciliation."""
        self._fences.check_entry()
        with self._fences.scope(identity):
            self._quarantined.add(_task_key(identity))
            with self._transaction() as db:
                db.execute("INSERT OR IGNORE INTO quarantine VALUES(?)", (_task_key(identity),))

    @contextmanager
    def mutation_scope(self, identity: TaskIdentity):
        """Host seam: A1 -> A2 -> F -> short snapshot OR short DB transaction.

        No production authority is integrated. All its mutation paths must join
        this scope and publish only after commit before activation can qualify.
        """
        with self._fences.scope(identity):
            with self._transaction() as db:
                self._require_usable(db, identity)
            yield

    def cleanup_scope(self, identity: TaskIdentity):
        """Serialize byte/grant disposal even when admission is quarantined.

        This permits cleanup only; it cannot acknowledge revocation/reassignment.
        """
        return self._fences.scope(identity)

    @asynccontextmanager
    async def async_mutation_scope(self, identity: TaskIdentity):
        def check():
            with self._transaction() as db:
                self._require_usable(db, identity)
        # Run the short check on the lease worker before handoff. Never hop to a
        # shared pool while F is held: its threads may already be waiting for F.
        async with self._fences.async_scope(identity, before_handoff=check):
            yield

    def current(self, approval: Approval) -> bool:
        self._fences.check_entry()
        with self._fences.scope(approval.identity):
            with self._transaction() as db:
                self._require_usable(db, approval.identity)
                current = self._current(db, approval)
            return current and self._admission_current(approval)

    def revoke(self, approval_id: str) -> None:
        self._fences.check_entry()
        # Discover immutable scope before taking F; never hold a writer awaiting F.
        with self._transaction() as db:
            row = db.execute("SELECT body FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            return
        identity = _load(row[0]).identity
        with self._fences.scope(identity), self._transaction() as db:
            self._require_usable(db, identity)
            db.execute("UPDATE approvals SET revoked=1 WHERE id=?", (approval_id,))

    @contextmanager
    def dispatch_scope(self, approval: Approval, byte_count: int):
        self._fences.check_entry()
        if type(byte_count) is not int or not 0 < byte_count <= 32_768:
            raise ArtifactDenied("ledger_release_size")
        with self._fences.scope(approval.identity):
            with self._transaction() as db:
                self._require_usable(db, approval.identity)
            if not self._admission_current(approval):
                raise ArtifactDenied("live_admission_changed")
            with self._transaction() as db:
                self._require_usable(db, approval.identity)
                if not self._current(db, approval):
                    raise ArtifactDenied("ledger_approval_revoked_or_spent")
                task = _task_key(approval.identity)
                count, size, max_count, max_bytes = db.execute(
                    "SELECT count,bytes,max_count,max_bytes FROM budgets WHERE task=?", (task,)).fetchone()
                if count + 1 > max_count or size + byte_count > max_bytes:
                    raise ArtifactDenied("ledger_task_budget")
                try:
                    db.execute("INSERT INTO releases VALUES(?,?)", (task, approval.request_id))
                except sqlite3.IntegrityError:
                    raise ArtifactDenied("ledger_request_spent") from None
                db.execute("UPDATE budgets SET count=count+1,bytes=bytes+? WHERE task=?", (byte_count, task))
                db.execute("UPDATE approvals SET state='consumed' WHERE id=?", (approval.approval_id,))
            # Only F spans transport. Unknown delivery remains consumed forever.
            yield
