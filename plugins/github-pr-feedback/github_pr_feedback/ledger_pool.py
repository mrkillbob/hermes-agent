"""Pool execution history fenced by exact-path lifecycle operations."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ledger import WorktreeSlotLease

from .worktree_lifecycle import PathLifecycle, WorkspaceIdentity, WorktreeRetirementRefused, ledger_operation


class PoolLedgerMixin:
    def _bind_pool_identity(self, operation):
        self._connection.execute("CREATE TABLE IF NOT EXISTS worktree_pool_identity ("
                                 "slot_id INTEGER PRIMARY KEY, store TEXT NOT NULL, "
                                 "workspace TEXT NOT NULL, generation TEXT NOT NULL, owner TEXT NOT NULL)")
        identity = operation.identity
        expected = (str(operation.lifecycle.state_path), identity.workspace, operation.generation, identity.encoded())
        row = self._connection.execute("SELECT store,workspace,generation,owner FROM worktree_pool_identity "
                                       "WHERE slot_id=?", (identity.slot_id,)).fetchone()
        if row is None and self._connection.execute(
                "SELECT 1 FROM worktree_pool_slots WHERE slot_id=?", (identity.slot_id,)).fetchone():
            raise WorktreeRetirementRefused("legacy_pool_identity_unestablished")
        if row is not None and tuple(row) != expected:
            raise WorktreeRetirementRefused("pool_slot_identity_changed")
        self._connection.execute("INSERT OR IGNORE INTO worktree_pool_identity VALUES (?,?,?,?,?)",
                                 (identity.slot_id, *expected))

    def _pool_mutation(self, slot_id):
        from .worktree_lifecycle import existing_operation
        if not self._connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                        "AND name='worktree_pool_identity'").fetchone():
            raise WorktreeRetirementRefused("legacy_pool_identity_unknown")
        row = self._connection.execute("SELECT store,workspace,generation,owner FROM worktree_pool_identity "
                                       "WHERE slot_id=?", (slot_id,)).fetchone()
        if row is None:
            raise WorktreeRetirementRefused("legacy_pool_identity_unknown")
        identity = WorkspaceIdentity(**json.loads(row[3])).canonical()
        if identity.ledger != str(Path(self.path).absolute()) or identity.slot_id != slot_id:
            raise WorktreeRetirementRefused("pool_ledger_identity_changed")
        lifecycle = PathLifecycle(Path(row[0]))
        active = existing_operation(lifecycle, identity)
        if active:
            active.verify()
            operation = active
        else:
            operation = lifecycle.reserve_materialization(identity)
        if operation.generation != row[2]:
            raise WorktreeRetirementRefused("pool_generation_changed")
        return nullcontext(operation) if active else operation

    def claim_worktree_slot(
        self,
        slot_id: int,
        *,
        owner_pid: int,
        head_sha: str | None,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> WorktreeSlotLease | None:
        """Claim one worktree-pool slot, fencing a not-yet-stale lease.

        Deliberately time-only, unlike claim_ci_run's PID-liveness check: a
        worktree-pool slot is handed off to a *dispatched Kanban agent task*
        that outlives the short-lived dispatcher process making this call, so
        the dispatcher's own PID going away is not evidence the slot is
        free -- only elapsed time against stale_before is. Callers must pass
        a stale_before comfortably longer than the longest task
        max_runtime_seconds that can hold a slot.
        """

        from .ledger import _aware_utc, WorktreeSlotLease
        operation = ledger_operation(self.path, slot_id)
        if owner_pid < 2:
            raise ValueError("worktree slot owner PID must identify a real process")
        claimed = _aware_utc(claimed_at, "claimed_at")
        stale = _aware_utc(stale_before, "stale_before")
        with self._transaction():
            self._bind_pool_identity(operation)
            row = self._connection.execute(
                "SELECT status, owner_pid, updated_at, lease_version, task_id "
                "FROM worktree_pool_slots WHERE slot_id = ?",
                (slot_id,),
            ).fetchone()
            version = 1
            if row is not None:
                if row[0] not in {"free", "leased"}:
                    raise WorktreeRetirementRefused("unknown_pool_execution_status")
                version = int(row[3]) + 1
                if row[0] == "leased":
                    # A bound Kanban task owns this checkout until the board
                    # positively reports that task terminal and
                    # reconcile_leases releases it.  Time-based reclamation is
                    # only safe for an unbound lease stranded mid-dispatch;
                    # retryable blocked/triage cards may legitimately outlive
                    # the timeout and must retain their exact-head workspace.
                    if row[4] is not None:
                        return None
                    updated_at = datetime.fromisoformat(str(row[2]))
                    if updated_at >= stale:
                        return None
                self._connection.execute(
                    "UPDATE worktree_pool_slots SET status = 'leased', owner_pid = ?, "
                    "head_sha = ?, claimed_at = ?, updated_at = ?, lease_version = ?, "
                    "task_id = NULL, board = NULL WHERE slot_id = ?",
                    (owner_pid, head_sha, claimed.isoformat(), claimed.isoformat(), version, slot_id),
                )
            else:
                self._connection.execute(
                    "INSERT INTO worktree_pool_slots (slot_id, status, owner_pid, head_sha, "
                    "claimed_at, updated_at, lease_version) VALUES (?, 'leased', ?, ?, ?, ?, ?)",
                    (slot_id, owner_pid, head_sha, claimed.isoformat(), claimed.isoformat(), version),
                )
        return WorktreeSlotLease(slot_id, version, owner_pid)

    def finish_worktree_slot(self, lease) -> None:
        """Release only a known exact identity; stale lease versions remain no-ops.

        Unknown/retiring/retired ownership is a refusal, never a lease release.
        """

        from .ledger import _aware_utc
        with self._pool_mutation(lease.slot_id), self._transaction():
            self._connection.execute(
                "UPDATE worktree_pool_slots SET status = 'free', updated_at = ? "
                "WHERE slot_id = ? AND lease_version = ? AND owner_pid = ? AND status = 'leased'",
                (
                    _aware_utc(datetime.now(UTC), "updated_at").isoformat(),
                    lease.slot_id,
                    lease.version,
                    lease.owner_pid,
                ),
            )

    def bind_worktree_slot_task(
        self,
        head_sha: str,
        task_id: str,
        board: str,
        *,
        slot_id: int | None = None,
        lease=None,
    ) -> None:
        """Bind only the exact issued lease/version and lifecycle identity."""

        if lease is None or slot_id != lease.slot_id:
            raise WorktreeRetirementRefused("exact_binding_lease_required")
        with self._pool_mutation(slot_id), self._transaction():
            self._connection.execute(
                "UPDATE worktree_pool_slots SET task_id=?,board=? WHERE slot_id=? "
                "AND head_sha=? AND owner_pid=? AND lease_version=? AND status='leased'",
                (task_id, board, slot_id, head_sha, lease.owner_pid, lease.version),
            )

    def leased_worktree_slots(self) -> tuple[dict[str, object], ...]:
        """List every currently-leased slot with an attached task binding."""

        rows = self._connection.execute(
            "SELECT slot_id, lease_version, owner_pid, task_id, board "
            "FROM worktree_pool_slots WHERE status = 'leased' AND task_id IS NOT NULL "
            "AND board IS NOT NULL"
        ).fetchall()
        return tuple(
            {
                "slot_id": int(row[0]),
                "lease_version": int(row[1]),
                "owner_pid": int(row[2]),
                "task_id": row[3],
                "board": row[4],
            }
            for row in rows
        )
