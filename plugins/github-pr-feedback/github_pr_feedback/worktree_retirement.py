"""Unregistered trusted operator API: prepare and retain by supported Git move.

Not a CLI, RPC, model tool or grant. Live invocation needs the operator's exact
selected target/adopted-producer approval. Old loaded code and arbitrary writers
are not fenced. Quarantine is retained mutable recovery and reclaims zero bytes.
"""
from contextlib import closing
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sqlite3
import unicodedata

from .worktree_lifecycle import PathLifecycle, WorktreeRetirementRefused
from .worktree_retirement_identity import (RetirementIdentity, observe_identity, _directory_identity,
                                           _file_identity, _git_run, verify_reference)
from .worktree_capacity import _target, WorktreeCapacityRejected


@dataclass(frozen=True)
class RetirementPlan:
    identity: RetirementIdentity
    expected_lease: dict | None
    preservation_reference: tuple
    owner_reference: tuple
    generation: str
    receipt_path: str
    receipt_digest: str


def _lease_snapshot(identity):
    if identity.ledger is None:
        if identity.slot_id is not None:
            raise WorktreeRetirementRefused("pool_identity_incomplete")
        return None
    _file_identity(identity.ledger)
    try:
        with closing(sqlite3.connect(Path(identity.ledger).as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM worktree_pool_slots WHERE slot_id=?", (identity.slot_id,)).fetchone()
            if row is None:
                raise WorktreeRetirementRefused("expected_pool_row_missing")
            return dict(row)
    except sqlite3.Error:
        raise WorktreeRetirementRefused("pool_ledger_unavailable") from None


def _verify_pool_binding(identity, generation):
    if identity.ledger is None:
        return
    try:
        with closing(sqlite3.connect(Path(identity.ledger).as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            bound = db.execute("SELECT store,workspace,generation,owner FROM worktree_pool_identity WHERE slot_id=?",
                               (identity.slot_id,)).fetchone()
    except sqlite3.Error:
        raise WorktreeRetirementRefused("legacy_pool_identity_unestablished") from None
    expected = (identity.control_store, identity.workspace, generation, identity.workspace_identity().encoded())
    if bound is None or tuple(bound) != expected:
        raise WorktreeRetirementRefused("legacy_pool_identity_unestablished")


def _verify_identity(identity):
    actual = observe_identity(identity.control_store, identity.repository, Path(identity.source),
                              Path(identity.workspace), ledger=identity.ledger, slot_id=identity.slot_id)
    if actual != identity:
        raise WorktreeRetirementRefused("retirement_identity_changed")


def _write_private(path, payload):
    """No-overwrite private evidence. An occupied/partial file remains for recovery."""
    path = Path(path)
    parent, ancestor, _ = _target(path.parent)
    if parent != ancestor or path.exists() or path.is_symlink():
        raise WorktreeRetirementRefused("absent_receipt_in_existing_private_directory_required")
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    from hermes_platform.host.facts import os_family
    if os_family() != "win32":
        descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return sha256(data).hexdigest()


def _receipt_paths(receipt_path, workspace, destination=None):
    """Evidence must remain outside both moved names, including path aliases."""
    plan = Path(receipt_path)
    result = Path(str(plan) + ".result")
    roots = [Path(workspace)] + ([Path(destination)] if destination is not None else [])
    for path in (plan, result):
        try:
            parent, ancestor, _ = _target(path.parent)
        except WorktreeCapacityRejected:
            raise WorktreeRetirementRefused("canonical_external_receipt_required") from None
        if not path.is_absolute() or parent != path.parent or ancestor != parent:
            raise WorktreeRetirementRefused("canonical_external_receipt_required")
        normalized = Path(unicodedata.normalize("NFC", str(path)).casefold())
        for root in roots:
            other = Path(unicodedata.normalize("NFC", str(root)).casefold())
            if normalized.is_relative_to(other) or other.is_relative_to(normalized):
                raise WorktreeRetirementRefused("receipt_overlaps_moving_workspace")
    return plan, result


def prepare_retirement(expected_identity, expected_lease, preservation_reference, *, owner_reference, receipt_path):
    """Read-only owner validation plus a private exact-plan receipt; no lifecycle change."""
    receipt_path, result_path = _receipt_paths(receipt_path, expected_identity.workspace)
    if result_path.exists() or result_path.is_symlink():
        raise WorktreeRetirementRefused("retirement_result_occupied")
    _verify_identity(expected_identity)
    if _lease_snapshot(expected_identity) != expected_lease:
        raise WorktreeRetirementRefused("pool_lease_changed")
    if expected_lease is not None and expected_lease.get("status") != "free":
        raise WorktreeRetirementRefused("pool_lease_not_free")
    preservation = verify_reference(preservation_reference, expected_identity)
    owner = verify_reference(owner_reference, expected_identity, owner=True)
    row = PathLifecycle(Path(expected_identity.control_store)).snapshot(expected_identity.workspace_identity())
    if expected_identity.ledger is not None and row is None:
        raise WorktreeRetirementRefused("legacy_pool_identity_unestablished")
    if row is not None and row["phase"] != "ready":
        raise WorktreeRetirementRefused("workspace_active_or_excluded")
    generation = row["generation"] if row else secrets.token_hex(24)
    _verify_pool_binding(expected_identity, generation)
    payload = {"kind": "feedback_retirement_plan/v1", "identity": asdict(expected_identity),
               "expected_lease": expected_lease, "preservation_reference": preservation,
               "owner_reference": owner, "generation": generation, "disk_reclaimed": 0}
    digest = _write_private(receipt_path, payload)
    return RetirementPlan(expected_identity, expected_lease, preservation, owner, generation,
                          str(receipt_path), digest)


def _fence_pool(plan, operation):
    """Cross-database journal: control intent first; history row is never rewritten."""
    identity = plan.identity
    if identity.ledger is None:
        return
    with closing(sqlite3.connect(identity.ledger, isolation_level=None, timeout=2)) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM worktree_pool_slots WHERE slot_id=?", (identity.slot_id,)).fetchone()
            if row is None or dict(row) != plan.expected_lease or row["status"] != "free":
                raise WorktreeRetirementRefused("pool_lease_changed")
            db.execute("CREATE TABLE IF NOT EXISTS worktree_pool_identity (slot_id INTEGER PRIMARY KEY, "
                       "store TEXT NOT NULL, workspace TEXT NOT NULL, generation TEXT NOT NULL, owner TEXT NOT NULL)")
            expected = (identity.control_store, identity.workspace, operation.generation,
                        operation.identity.encoded())
            bound = db.execute("SELECT store,workspace,generation,owner FROM worktree_pool_identity WHERE slot_id=?",
                               (identity.slot_id,)).fetchone()
            if bound is None:
                raise WorktreeRetirementRefused("legacy_pool_identity_unestablished")
            if tuple(bound) != expected:
                raise WorktreeRetirementRefused("pool_slot_identity_changed")
            db.commit()
        except BaseException:
            db.rollback()
            raise


def retire_to_quarantine(plan, destination):
    """Fence adopted owners, then retain all bytes by Git move. Never delete/prune/reset."""
    identity, destination = plan.identity, Path(destination)
    _, result_path = _receipt_paths(plan.receipt_path, identity.workspace, destination)
    _file_identity(plan.receipt_path)
    if sha256(Path(plan.receipt_path).read_bytes()).hexdigest() != plan.receipt_digest:
        raise WorktreeRetirementRefused("retirement_plan_changed")
    prepared = json.loads(Path(plan.receipt_path).read_bytes())
    expected = {"kind": "feedback_retirement_plan/v1", "identity": asdict(identity),
                "expected_lease": plan.expected_lease, "preservation_reference": plan.preservation_reference,
                "owner_reference": plan.owner_reference, "generation": plan.generation, "disk_reclaimed": 0}
    if prepared != json.loads(json.dumps(expected)):
        raise WorktreeRetirementRefused("retirement_plan_identity_changed")
    verify_reference(plan.preservation_reference, identity)
    verify_reference(plan.owner_reference, identity, owner=True)
    if result_path.exists() or result_path.is_symlink():
        raise WorktreeRetirementRefused("retirement_result_occupied")
    target, ancestor, device = _target(destination)
    if (target != destination or target == ancestor or destination.exists()
            or ancestor != destination.parent or device != str(identity.workspace_directory[0])
            or destination.is_relative_to(Path(identity.workspace))):
        raise WorktreeRetirementRefused("absent_same_device_destination_required")
    from .caller_repository import CallerRepository, repository_scope
    from .cli import _exclusive_scan_lock
    context = type("Source", (), {"session_cwd": lambda self, **kw: identity.source})()
    binding = CallerRepository.resolve(context, identity.repository)
    control_home = Path(identity.control_store).parent.parent
    with repository_scope(binding), _exclusive_scan_lock(control_home) as acquired:
        if not acquired:
            raise WorktreeRetirementRefused("caller_operation_active")
        lifecycle = PathLifecycle(Path(identity.control_store))
        with lifecycle.reserve_retirement(identity.workspace_identity(), plan.generation) as operation:
            _verify_identity(identity)
            if _lease_snapshot(identity) != plan.expected_lease:
                raise WorktreeRetirementRefused("pool_lease_changed")
            _fence_pool(plan, operation)
            operation.mark_destination(destination)
            # No SQLite writer transaction survives this point.
            _git_run(Path(identity.source), "worktree", "move", "--", identity.workspace, str(destination))
            if _directory_identity(destination) != identity.workspace_directory:
                raise WorktreeRetirementRefused("moved_directory_identity_uncertain")
            moved = observe_identity(identity.control_store, identity.repository, Path(identity.source), destination,
                                     ledger=identity.ledger, slot_id=identity.slot_id)
            if (moved.admin_directory != identity.admin_directory or moved.admin_dir != identity.admin_dir
                    or moved.physical_head != identity.physical_head):
                raise WorktreeRetirementRefused("moved_git_identity_uncertain")
            result = {"kind": "feedback_retained_move/v1", "identity": asdict(identity),
                      "destination": asdict(moved), "generation": plan.generation,
                      "preservation_reference": plan.preservation_reference, "owner_reference": plan.owner_reference,
                      "disk_reclaimed": 0, "retained_mutable": True}
            _write_private(result_path, result)
            operation.complete_move(destination)
            return result
