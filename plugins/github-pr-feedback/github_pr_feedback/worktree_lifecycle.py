"""Exact-path lifecycle fencing in the existing control capacity database.

No budgets, queue or native worktree authority is added. Previously loaded code
and unrelated filesystem writers do not consult this cooperative boundary.
"""
from __future__ import annotations

from contextlib import closing
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import secrets
import sqlite3
import unicodedata

from .worktree_capacity import WorktreeCapacityAdmission, WorktreeCapacityPolicy, _target

_ACTIVE = ContextVar("feedback_workspace_operations", default=())
_COLUMNS = ["path", "owner", "generation", "phase", "token", "destination", "operation"]


class WorktreeRetirementRefused(RuntimeError):
    """A protected path is never pool exhaustion and must not trigger overflow."""


class WorktreeMaterializationBusy(RuntimeError):
    """Same physical owner is currently preparing this candidate; no exclusion is inferred."""


@dataclass(frozen=True)
class WorkspaceIdentity:
    repository: str
    source: str
    workspace: str
    ledger: str | None = None
    slot_id: int | None = None

    def canonical(self):
        from .caller_repository import _REPO
        if not isinstance(self.repository, str) or not _REPO.fullmatch(self.repository):
            raise WorktreeRetirementRefused("repository_identity_required")
        values = {}
        for name in ("source", "workspace", "ledger"):
            value = getattr(self, name)
            if value is None and name == "ledger":
                continue
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise WorktreeRetirementRefused("absolute_identity_required")
            path = Path(value)
            if name == "ledger":
                parent, _, _ = _target(path.parent)
                values[name] = str(parent / path.name)
            else:
                values[name] = str(_target(path)[0])
        if (self.slot_id is None) != (self.ledger is None):
            raise WorktreeRetirementRefused("pool_identity_incomplete")
        if self.slot_id is not None and (type(self.slot_id) is not int or self.slot_id < 0):
            raise WorktreeRetirementRefused("pool_identity_invalid")
        return WorkspaceIdentity(self.repository.casefold(), values["source"], values["workspace"],
                                 values.get("ledger"), self.slot_id)

    def path_owner(self):
        identity = self.canonical()
        return json.dumps({"repository": identity.repository, "source": identity.source,
                           "workspace": identity.workspace}, sort_keys=True, separators=(",", ":"))

    def encoded(self):
        return json.dumps(asdict(self.canonical()), sort_keys=True, separators=(",", ":"))


@dataclass
class PathOperation:
    lifecycle: PathLifecycle
    identity: WorkspaceIdentity
    generation: str
    token: str
    phase: str
    _context_token: object = None

    def __enter__(self):
        if self._context_token is not None:
            raise WorktreeRetirementRefused("operation_already_entered")
        self._context_token = _ACTIVE.set((*_ACTIVE.get(), self))
        return self

    def __exit__(self, exception_type, exception, traceback):
        try:
            if self.phase == "materializing":
                self.lifecycle._transition(self, "ready" if exception_type is None else "uncertain")
            # Retirement intent never automatically clears, including on failure.
        finally:
            _ACTIVE.reset(self._context_token)
            self._context_token = None
        return False

    def cancel_before_mutation(self):
        """Trusted producer's definite pre-workspace-mutation failure only."""
        if self.phase != "materializing":
            raise WorktreeRetirementRefused("materialization_operation_required")
        self.lifecycle._transition(self, "ready")
        self.phase = "cancelled"

    def mark_destination(self, destination: Path):
        self.lifecycle._destination(self, str(destination))

    def complete_move(self, destination: Path):
        """Internal publication step; the operator API verifies supported Git move first."""
        if self.phase != "retiring":
            raise WorktreeRetirementRefused("retirement_operation_required")
        if self.lifecycle.snapshot(self.identity)["destination"] != str(destination):
            raise WorktreeRetirementRefused("retained_destination_not_reserved")
        self.lifecycle._transition(self, "retired", str(destination))
        self.phase = "retired"

    def verify(self):
        row = self.lifecycle.snapshot(self.identity)
        if (not row or row["operation"] != self.identity.encoded()
                or any(row[key] != getattr(self, key) for key in ("generation", "token", "phase"))):
            raise WorktreeRetirementRefused("operation_fence_changed")


class PathLifecycle:
    def __init__(self, state_path: Path):
        path = Path(state_path)
        if not path.is_absolute():
            raise WorktreeRetirementRefused("absolute_control_store_required")
        parent, _, _ = _target(path.parent)
        self.state_path = parent / path.name

    def _connect(self, *, create=False):
        if not self.state_path.exists() and not self.state_path.is_symlink() and not create:
            return None
        if create:
            _target(self.state_path.parent)
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # Existing schema/path checks are reused; this does not charge capacity.
        admission = WorktreeCapacityAdmission(self.state_path, WorktreeCapacityPolicy())
        db = admission._connect()
        try:
            if create:
                db.execute("CREATE TABLE IF NOT EXISTS workspace_lifecycle (path TEXT PRIMARY KEY, "
                           "owner TEXT NOT NULL, generation TEXT NOT NULL, phase TEXT NOT NULL, "
                           "token TEXT NOT NULL, destination TEXT, operation TEXT NOT NULL)")
                db.commit()
            columns = [row[1] for row in db.execute("PRAGMA table_info(workspace_lifecycle)")]
            if columns and columns != _COLUMNS:
                raise WorktreeRetirementRefused("unknown_lifecycle_schema")
            return db
        except BaseException:
            db.close()
            raise

    def snapshot(self, identity):
        identity = identity.canonical()
        db = self._connect()
        if db is None:
            return None
        with closing(db):
            if not db.execute("PRAGMA table_info(workspace_lifecycle)").fetchone():
                return None
            row = db.execute("SELECT * FROM workspace_lifecycle WHERE path=?", (identity.workspace,)).fetchone()
            if row is None:
                return None
            result = dict(zip(_COLUMNS, row, strict=True))
            if result["owner"] != identity.path_owner():
                raise WorktreeRetirementRefused("workspace_owner_changed")
            return result

    def reserve_materialization(self, identity):
        return self._reserve(identity, "materializing")

    def reserve_retirement(self, identity, expected_generation):
        return self._reserve(identity, "retiring", expected_generation)

    def _reserve(self, identity, phase, expected_generation=None):
        identity = identity.canonical()
        with closing(self._connect(create=True)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                alias = unicodedata.normalize("NFC", identity.workspace).casefold()
                for path, destination in db.execute("SELECT path,destination FROM workspace_lifecycle"):
                    if destination and unicodedata.normalize("NFC", destination).casefold() == alias:
                        raise WorktreeRetirementRefused("retained_destination_protected")
                    if path != identity.workspace and unicodedata.normalize("NFC", path).casefold() == alias:
                        raise WorktreeRetirementRefused("workspace_alias_protected")
                row = db.execute("SELECT owner,generation,phase,operation FROM workspace_lifecycle WHERE path=?",
                                 (identity.workspace,)).fetchone()
                if row and phase == "retiring" and row[3] != identity.encoded():
                    raise WorktreeRetirementRefused("exact_lifecycle_identity_required")
                if row and row[0] == identity.path_owner() and row[2] == "materializing" and phase == "materializing":
                    raise WorktreeMaterializationBusy("workspace_materialization_busy")
                if row and (row[0] != identity.path_owner() or row[2] != "ready"):
                    raise WorktreeRetirementRefused("workspace_active_or_excluded")
                generation = row[1] if row else (expected_generation or secrets.token_hex(24))
                if expected_generation is not None and generation != expected_generation:
                    raise WorktreeRetirementRefused("workspace_generation_changed")
                token = secrets.token_hex(24)
                db.execute("INSERT INTO workspace_lifecycle VALUES (?,?,?,?,?,NULL,?) "
                           "ON CONFLICT(path) DO UPDATE SET phase=excluded.phase,token=excluded.token,operation=excluded.operation",
                           (identity.workspace, identity.path_owner(), generation, phase, token, identity.encoded()))
                db.commit()
                return PathOperation(self, identity, generation, token, phase)
            except BaseException:
                db.rollback()
                raise

    def _destination(self, operation, destination):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            alias = unicodedata.normalize("NFC", destination).casefold()
            for path, retained in db.execute("SELECT path,destination FROM workspace_lifecycle"):
                if any(value and unicodedata.normalize("NFC", value).casefold() == alias
                       for value in (path, retained)):
                    raise WorktreeRetirementRefused("destination_operation_active_or_excluded")
            cursor = db.execute("UPDATE workspace_lifecycle SET destination=? WHERE path=? "
                                "AND owner=? AND generation=? AND token=? AND phase='retiring' AND destination IS NULL",
                                (destination, operation.identity.workspace, operation.identity.path_owner(),
                                 operation.generation, operation.token))
            if cursor.rowcount != 1:
                db.rollback()
                raise WorktreeRetirementRefused("destination_fence_changed")
            db.commit()

    def _transition(self, operation, phase, destination=None):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE workspace_lifecycle SET phase=?,destination=? "
                                "WHERE path=? AND owner=? AND generation=? AND token=? AND phase=?",
                                (phase, destination, operation.identity.workspace, operation.identity.path_owner(),
                                 operation.generation, operation.token, operation.phase))
            if cursor.rowcount != 1:
                db.rollback()
                raise WorktreeRetirementRefused("operation_fence_changed")
            db.commit()


def materialization_for(source, workspace, repository, *, capacity=None, ledger=None, slot_id=None):
    """Mandatory owner exclusion, even when optional capacity charging is absent."""
    # Budget injection never selects a different ownership authority.
    state_path = canonical_control_store()
    state_path.parent.parent.mkdir(parents=True, exist_ok=True)
    identity = WorkspaceIdentity(repository, str(Path(source).absolute()), str(Path(workspace).absolute()),
                                 str(Path(ledger).absolute()) if ledger is not None else None, slot_id)
    return PathLifecycle(state_path).reserve_materialization(identity)


def ledger_operation(ledger, slot_id):
    matching = [op for op in _ACTIVE.get() if op.phase == "materializing"
                and op.identity.ledger == str(Path(ledger).absolute()) and op.identity.slot_id == slot_id]
    if len(matching) != 1:
        raise WorktreeRetirementRefused("exact_materialization_operation_required")
    matching[0].verify()
    return matching[0]


def existing_operation(lifecycle, identity):
    return next((op for op in _ACTIVE.get() if op.lifecycle.state_path == lifecycle.state_path
                 and op.identity == identity and op.phase == "materializing"), None)


def canonical_control_store():
    from hermes_constants import get_default_hermes_root
    home, _, _ = _target(get_default_hermes_root().resolve())
    return home / "state" / "worktree-capacity.sqlite3"


def scoped_materialization(source, workspace, repository):
    """Only a nested public branch helper may reuse its owning operation."""
    from contextlib import nullcontext
    lifecycle = PathLifecycle(canonical_control_store())
    identity = WorkspaceIdentity(repository, str(Path(source).absolute()), str(Path(workspace).absolute())).canonical()
    active = existing_operation(lifecycle, identity)
    if active is not None:
        active.verify()
        return nullcontext(active), False
    return materialization_for(source, workspace, repository), True
