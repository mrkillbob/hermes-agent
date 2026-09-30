"""Persistent, short-transaction admission for controlled workspace creators.

This NEW policy holds conservative estimated future-work budgets after allocation.
It is not physical byte measurement or Git ownership proof. All callers must use
canonical CONTROL_HOME/state/worktree-capacity.sqlite3 before workspace mutation.
Older/uncontrolled writers are not fenced; retained/uncertain budgets never expire
or release automatically. Owner reconciliation is deliberately outside this API.
"""

from __future__ import annotations

import os
import secrets
import shutil
import sqlite3
import stat
import unicodedata
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Self

GIB = 1024**3


class WorktreeCapacityRejected(RuntimeError):
    """Do not catch this as pool exhaustion or redirect it into overflow."""


@dataclass(frozen=True)
class WorktreeCapacityPolicy:
    reserve_bytes: int = 2 * GIB
    requested_bytes: int = 8 * GIB

    def __post_init__(self) -> None:
        for value in (self.reserve_bytes, self.requested_bytes):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("capacity budgets must be nonnegative integers")
        if self.requested_bytes > 2**63 - 1:
            raise ValueError("requested_bytes must fit a SQLite signed integer")
        if self.requested_bytes == 0:
            raise ValueError("requested_bytes must be positive")


def _reject(reason: str) -> WorktreeCapacityRejected:
    return WorktreeCapacityRejected(f"worktree_capacity: {reason}")


def _target(path: Path) -> tuple[Path, Path, str]:
    # Resolving first would conceal symlinks/junctions. Reject them at every level.
    target = Path(os.path.abspath(path.expanduser()))
    ancestor: Path | None = None
    for part in (target, *target.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise _reject("path metadata unavailable") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise _reject("symlink or reparse path protected")
        if ancestor is None:
            if not stat.S_ISDIR(info.st_mode):
                raise _reject("workspace ancestor is not a directory")
            ancestor = part
    if ancestor is None:
        raise _reject("no existing filesystem ancestor")
    try:
        device = str(ancestor.stat().st_dev)
    except OSError:
        raise _reject("filesystem identity unavailable") from None
    return target, ancestor, device


@dataclass(frozen=True)
class WorktreeCapacityHandle:
    admission: WorktreeCapacityAdmission
    path: str
    device: str
    token: str

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exception_type, exception, traceback) -> bool:
        self.admission._finish(self, successful=exception_type is None)
        return False


class WorktreeCapacityAdmission:
    """Explicit common state path, one short SQLite transaction per operation.

    reserve() activates an exclusive fenced operation for a path. Context success
    retains its budget and makes the path available for a subsequent controlled
    operation without double charge. Failure/partial work becomes uncertain and
    cannot be adopted or released. Caller must independently verify Git/lease/
    board/config/process ownership before reusing registered physical work.
    """

    def __init__(
        self,
        state_path: Path,
        policy: WorktreeCapacityPolicy,
        *,
        disk_usage: Callable = shutil.disk_usage,
    ):
        parent, ancestor, _ = _target(state_path.parent)
        if parent != ancestor:
            raise _reject("CONTROL state directory must already exist")
        self.state_path = parent / state_path.name
        self.policy = policy
        self._probe = disk_usage
        self._initialize()

    def _initialize(self) -> None:
        if self.state_path.exists() or self.state_path.is_symlink():
            self._connect().close()
            return
        try:
            fd = os.open(self.state_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            raise _reject(
                "concurrent initialization; retry after verification"
            ) from None
        os.close(fd)
        try:
            with closing(sqlite3.connect(self.state_path)) as db:
                db.execute("PRAGMA user_version=1")
                db.execute(
                    "CREATE TABLE reservations (path TEXT PRIMARY KEY, device TEXT NOT NULL, token TEXT NOT NULL, requested_bytes INTEGER NOT NULL CHECK(requested_bytes>0), phase TEXT NOT NULL CHECK(phase IN ('allocating','retained','uncertain')))"
                )
        except sqlite3.Error:
            raise _reject("partial registry initialization protected") from None

    def _connect(self) -> sqlite3.Connection:
        _, ancestor, _ = _target(self.state_path.parent)
        if ancestor != self.state_path.parent:
            raise _reject("registry parent protected")
        try:
            info = self.state_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                raise _reject("registry link or nonregular file protected")
            db = sqlite3.connect(
                self.state_path.as_uri() + "?mode=rw", uri=True, timeout=2
            )
            columns = [r[1] for r in db.execute("PRAGMA table_info(reservations)")]
            if db.execute("PRAGMA user_version").fetchone()[0] != 1 or columns != [
                "path",
                "device",
                "token",
                "requested_bytes",
                "phase",
            ]:
                db.close()
                raise _reject("unknown registry schema protected")
            return db
        except (OSError, sqlite3.Error):
            raise _reject("registry unavailable; protected") from None

    def is_unregistered_existing(self, workspace_path: Path) -> bool:
        """Read-only selection hint; final reserve remains the admission authority.

        Only an exact canonical existing directory with no registry row can be
        skipped as preserved legacy work. Unknown metadata/schema and aliases
        reject. Registered uncertain/allocating paths are never classified as
        legacy, so their ordinary reserve failures remain hard gates.
        """
        target, ancestor, device = _target(workspace_path)
        identity = unicodedata.normalize("NFC", str(target)).casefold()
        with closing(self._connect()) as db:
            for (registered,) in db.execute(
                "SELECT path FROM reservations WHERE device=?", (device,)
            ):
                if (registered != str(target)
                        and unicodedata.normalize("NFC", registered).casefold() == identity):
                    raise _reject("case or Unicode path alias protected")
            row = db.execute("SELECT 1 FROM reservations WHERE path=?", (str(target),)).fetchone()
            return row is None and target == ancestor

    def reserve(self, workspace_path: Path) -> WorktreeCapacityHandle:
        target, ancestor, device = _target(workspace_path)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            # APFS/Windows can alias distinct spellings before either path exists.
            # Conservatively reject case/Unicode aliases even on sensitive devices;
            # raw exact-path reuse retains its existing fence and charge.
            identity = unicodedata.normalize("NFC", str(target)).casefold()
            for (registered,) in db.execute(
                "SELECT path FROM reservations WHERE device=?", (device,)
            ):
                if (
                    registered != str(target)
                    and unicodedata.normalize("NFC", registered).casefold() == identity
                ):
                    raise _reject("case or Unicode path alias protected")
            row = db.execute(
                "SELECT device,requested_bytes,phase FROM reservations WHERE path=?",
                (str(target),),
            ).fetchone()
            token = secrets.token_hex(16)
            if row:
                if row[0] != device or row[1] != self.policy.requested_bytes:
                    raise _reject("reservation identity or policy mismatch protected")
                if row[2] != "retained" or not target.exists():
                    raise _reject(
                        "allocating, uncertain or missing workspace protected"
                    )
            elif target.exists():
                raise _reject("unaccounted legacy workspace protected")
            try:
                available = self._probe(ancestor).free
            except (OSError, ValueError, AttributeError, TypeError):
                raise _reject("capacity probe unavailable") from None
            if (
                isinstance(available, bool)
                or not isinstance(available, int)
                or available < 0
            ):
                raise _reject("capacity probe invalid")
            charged = db.execute(
                "SELECT COALESCE(SUM(requested_bytes),0) FROM reservations WHERE device=?",
                (device,),
            ).fetchone()[0]
            additional = 0 if row else self.policy.requested_bytes
            if available < self.policy.reserve_bytes + charged + additional:
                raise _reject(
                    f"insufficient space available={available} held={charged} requested={additional} reserve={self.policy.reserve_bytes} device={device}"
                )
            if row:
                db.execute(
                    "UPDATE reservations SET token=?,phase='allocating' WHERE path=?",
                    (token, str(target)),
                )
            else:
                db.execute(
                    "INSERT INTO reservations VALUES (?,?,?,?,'allocating')",
                    (str(target), device, token, self.policy.requested_bytes),
                )
            db.commit()
            return WorktreeCapacityHandle(self, str(target), device, token)
        except sqlite3.Error:
            raise _reject("atomic admission unavailable; protected") from None
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def _finish(self, handle: WorktreeCapacityHandle, *, successful: bool) -> None:
        requested_success = successful
        if successful:
            try:
                target, ancestor, device = _target(Path(handle.path))
                successful = target == ancestor and device == handle.device
            except (OSError, WorktreeCapacityRejected):
                successful = False
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE reservations SET phase=? WHERE path=? AND device=? AND token=? AND phase='allocating'",
                (
                    "retained" if successful else "uncertain",
                    handle.path,
                    handle.device,
                    handle.token,
                ),
            )
            if cursor.rowcount != 1:
                raise _reject("stale reservation fence protected")
            db.commit()
        except sqlite3.Error:
            raise _reject("reservation update unavailable; protected") from None
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()
        if requested_success and not successful:
            raise _reject(
                "completed allocation lacks valid physical workspace; retained as uncertain"
            )


def control_capacity_admission(
    *,
    policy: WorktreeCapacityPolicy | None = None,
) -> WorktreeCapacityAdmission:
    """Every production allocator shares the canonical control root, across profiles.

    Explicit injection on low-level repositories is the test adapter seam; managed
    scan, repair, review, maintenance and overflow callers always supply admission.
    Unknown pre-rollout trees remain protected, never silently adopted.
    """
    from hermes_constants import get_default_hermes_root

    home, ancestor, _ = _target(get_default_hermes_root())
    if home != ancestor:
        raise _reject("canonical CONTROL home must already exist")
    state = home / "state"
    _target(state)
    state.mkdir(exist_ok=True)
    return WorktreeCapacityAdmission(
        state / "worktree-capacity.sqlite3", policy or WorktreeCapacityPolicy()
    )
