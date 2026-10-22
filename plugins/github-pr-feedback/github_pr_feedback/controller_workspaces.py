"""Physical materialization under unconditional exact-path lifecycle admission."""
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
import re

from .worktree_lifecycle import materialization_for, scoped_materialization, WorktreeMaterializationBusy
from .controller import (PreparedWorktree, _SHA, _receipt_branch, _pool_source_namespace,
                         _pool_ledger_slot, WorktreePoolSlotDirty, WorktreePoolExhausted)


def prepare_local(self, path, receipt):
    if self._binding is not None:
        self._binding.verify_workspace(path, receipt.repository)
    if not _SHA.fullmatch(receipt.head_sha):
        raise ValueError("head SHA is not a full Git object ID")
    workspace = (
        self._worktree_root
        / sha256("\x00".join(map(str, receipt.key)).encode("utf-8")).hexdigest()
    )
    with materialization_for(path, workspace, receipt.repository, capacity=self._capacity) as operation:
        try:
            admission = self._capacity.reserve(workspace) if self._capacity else nullcontext()
            with admission:
                self._ensure_exact_head(path, receipt)

                branch = self.prepare_receipt_branch(
                    path, receipt, object_already_verified=True
                )
                # The nested branch helper marks this same owner at its Git
                # mutation boundary; even an existing ref precedes mkdir here.
                operation.mutation_started = True
                self._worktree_root.mkdir(parents=True, exist_ok=True)
                if workspace.is_symlink():
                    raise RuntimeError(
                        "deterministic receipt worktree path must not be a symlink"
                    )
                if workspace.exists():
                    if self._binding is not None:
                        self._binding.verify_workspace(workspace, receipt.repository)
                    self._verify_worktree(workspace, receipt.head_sha)
                else:
                    self._run(
                        [
                            "git",
                            "-C",
                            str(path),
                            "worktree",
                            "add",
                            "--quiet",
                            str(workspace),
                            branch,
                        ]
                    )
                    self._verify_worktree(workspace, receipt.head_sha)
                self._link_governed_venv(path, workspace)
                return PreparedWorktree(workspace.resolve(), branch, receipt.head_sha)
        except BaseException:
            if not operation.mutation_started:
                operation.cancel_before_mutation()
            raise


def prepare_pool(self, path, receipt):
    if self._binding is not None:
        self._binding.verify_workspace(path, receipt.repository)
    if not _SHA.fullmatch(receipt.head_sha):
        raise ValueError("head SHA is not a full Git object ID")
    now, owner_pid = self._clock(), self._owner_pid()
    namespace = _pool_source_namespace(path)
    for slot_id in range(self._slot_count):
        candidate = self._slot_path(receipt, slot_id, namespace)
        ledger_slot_id = _pool_ledger_slot(namespace, slot_id)
        try:
            operation = materialization_for(path, candidate, receipt.repository, capacity=self._capacity,
                                            ledger=self._ledger.path, slot_id=ledger_slot_id)
        except WorktreeMaterializationBusy:
            continue
        with operation:
            if self._capacity and self._capacity.is_unregistered_existing(candidate):
                continue
            lease = self._ledger.claim_worktree_slot(
                ledger_slot_id, owner_pid=owner_pid, head_sha=receipt.head_sha,
                claimed_at=now, stale_before=now - self._lease_timeout)
            if lease is None:
                continue
            physical = False
            try:
                admission = self._capacity.reserve(candidate) if self._capacity else nullcontext()
                with admission:
                    self._ensure_exact_head(path, receipt)
                    physical = True
                    workspace = self._prepare_slot(path, slot_id, receipt, namespace=namespace)
            except WorktreePoolSlotDirty:
                self._ledger.finish_worktree_slot(lease)
                continue
            except BaseException:
                self._ledger.finish_worktree_slot(lease)
                if not physical:
                    operation.cancel_before_mutation()
                raise
            self._active_ledger_slots[receipt.key] = lease
            return PreparedWorktree(workspace, _receipt_branch(receipt), receipt.head_sha)
    raise WorktreePoolExhausted(f"all {self._slot_count} worktree-pool slots are leased or contain preserved work")


def prepare_branch(self, path, receipt, *, object_already_verified=False):
    scope, owns_operation = scoped_materialization(
        path, _local_workspace(self, receipt), receipt.repository
    )
    with scope as operation:
        try:
            if not _SHA.fullmatch(receipt.head_sha):
                raise ValueError("head SHA is not a full Git object ID")
            branch = _receipt_branch(receipt)
            if not object_already_verified:
                self._ensure_exact_head(path, receipt)
            current = self._run(
                [
                    "git",
                    "-C",
                    str(path),
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"refs/heads/{branch}^{{commit}}",
                ],
                missing_ok=True,
            ).strip()
            if current:
                if current.casefold() != receipt.head_sha.casefold():
                    raise RuntimeError(
                        "deterministic receipt branch collides with another commit"
                    )
                return branch
            # A failed Git response may still have created the ref; from this
            # point the lifecycle row must remain uncertain until reconciliation.
            operation.mutation_started = True
            self._run(["git", "-C", str(path), "branch", branch, receipt.head_sha])
            verified = self._run(
                [
                    "git",
                    "-C",
                    str(path),
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"refs/heads/{branch}^{{commit}}",
                ]
            ).strip()
            if verified.casefold() != receipt.head_sha.casefold():
                raise RuntimeError("receipt branch does not point at the required head")
            return branch
        except BaseException:
            if owns_operation and not operation.mutation_started:
                operation.cancel_before_mutation()
            raise


def _local_workspace(self, receipt):
    return self._worktree_root / sha256("\0".join(map(str, receipt.key)).encode("utf-8")).hexdigest()


def prepare_maintenance(
    self,
    repository_path: Path,
    repository: str,
    head_sha: str,
    lane: str,
) -> Path:
    """Materialize one isolated, immutable workspace for a maintenance lane."""

    if self._binding is not None:
        self._binding.verify_workspace(repository_path, repository)
    if not _SHA.fullmatch(head_sha):
        raise ValueError("head SHA is not a full Git object ID")
    if not repository.strip() or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", lane):
        raise ValueError("maintenance workspace identity is invalid")
    digest = sha256(f"{repository}\0{head_sha}\0{lane}".encode()).hexdigest()
    workspace = self._worktree_root / f"maintenance-{digest}"
    with materialization_for(repository_path, workspace, repository, capacity=self._capacity) as operation:
        physical = False
        try:
            if self._binding is not None and workspace.exists():
                self._binding.verify_workspace(workspace, repository)
            admission = self._capacity.reserve(workspace) if self._capacity else nullcontext()
            with admission:
                self._run(
                    [
                        "git",
                        "-C",
                        str(repository_path),
                        "cat-file",
                        "-e",
                        f"{head_sha}^{{commit}}",
                    ]
                )
                branch = f"hermes/release-maintenance/{digest[:20]}"
                current = self._run(
                    [
                        "git",
                        "-C",
                        str(repository_path),
                        "rev-parse",
                        "--verify",
                        "--quiet",
                        f"refs/heads/{branch}^{{commit}}",
                    ],
                    missing_ok=True,
                ).strip()
                if current and current.casefold() != head_sha.casefold():
                    raise RuntimeError("maintenance branch collides with another commit")
                if not current:
                    physical = True
                    self._run(["git", "-C", str(repository_path), "branch", branch, head_sha])
                # This can create the worktree parent, so later failures keep
                # the lifecycle row uncertain even if Git add has not run.
                physical = True
                self._worktree_root.mkdir(parents=True, exist_ok=True)
                if workspace.is_symlink():
                    raise RuntimeError("maintenance worktree path must not be a symlink")
                created = False
                if not workspace.exists():
                    self._run(
                        [
                            "git",
                            "-C",
                            str(repository_path),
                            "worktree",
                            "add",
                            "--quiet",
                            str(workspace),
                            branch,
                        ]
                    )
                    created = True
                if created:
                    from hermes_cli.worktree_environment import bootstrap_worktree_environments

                    bootstrap_worktree_environments(
                        Path(repository_path), workspace, environment_names=(".venv",)
                    )
                self._verify_worktree(workspace, head_sha)
                return workspace.resolve()
        except BaseException:
            if not physical:
                operation.cancel_before_mutation()
            raise
