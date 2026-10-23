"""Read-only exact Git/physical identity for trusted pool retirement preparation."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess

from .caller_repository import CallerRepository, reject_git_overrides
from .worktree_capacity import _target
from .worktree_lifecycle import (PathLifecycle, WorkspaceIdentity, WorktreeRetirementRefused,
                                 canonical_control_store)


def _git_run(root, *args):
    from hermes_cli._subprocess_compat import noninteractive_repo_git_env
    reject_git_overrides()
    env = noninteractive_repo_git_env(root)
    if env is None:
        raise WorktreeRetirementRefused("git_environment_unavailable")
    try:
        result = subprocess.run(["git", "-C", str(root), *args], env=env, check=False,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                encoding="utf-8", errors="strict", timeout=10)
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        raise WorktreeRetirementRefused("git_result_uncertain") from None
    if result.returncode:
        raise WorktreeRetirementRefused("git_operation_refused_or_uncertain")
    return result.stdout.strip()


def _file_identity(path):
    path = Path(path)
    _target(path.parent)
    try:
        info = path.lstat()
    except OSError:
        raise WorktreeRetirementRefused("file_identity_unavailable") from None
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise WorktreeRetirementRefused("regular_unaliased_file_required")
    return (info.st_dev, info.st_ino)


def _directory_identity(path):
    target, ancestor, _ = _target(Path(path))
    if target != ancestor:
        raise WorktreeRetirementRefused("existing_exact_directory_required")
    info = target.stat()
    return (info.st_dev, info.st_ino)


@dataclass(frozen=True)
class RetirementIdentity:
    control_store: str
    control_file: tuple
    repository: str
    source: str
    source_directory: tuple
    common_dir: str
    common_directory: tuple
    workspace: str
    workspace_directory: tuple
    admin_dir: str
    admin_directory: tuple
    physical_head: str
    ledger: str | None = None
    slot_id: int | None = None
    ledger_file: tuple | None = None

    def workspace_identity(self):
        return WorkspaceIdentity(self.repository, self.source, self.workspace, self.ledger, self.slot_id).canonical()


def observe_identity(control_store, repository, source, workspace, *, ledger=None, slot_id=None):
    """Observe real registration; never infer a modern slot from a legacy pathname."""
    if Path(control_store) != canonical_control_store():
        raise WorktreeRetirementRefused("canonical_control_store_required")
    source, workspace = Path(source), Path(workspace)
    _directory_identity(source)
    physical = _directory_identity(workspace)
    binding = CallerRepository.resolve(type("Source", (), {"session_cwd": lambda self, **kw: str(source)})(), repository)
    if binding.root != source or Path(_git_run(workspace, "rev-parse", "--show-toplevel")) != workspace:
        raise WorktreeRetirementRefused("exact_source_and_workspace_roots_required")
    common = Path(_git_run(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    admin = Path(_git_run(workspace, "rev-parse", "--absolute-git-dir"))
    if common != binding.common_dir or not admin.is_relative_to(common / "worktrees"):
        raise WorktreeRetirementRefused("linked_worktree_registration_required")
    _file_identity(workspace / ".git")
    entries = _git_run(source, "worktree", "list", "--porcelain", "-z").split("\0\0")
    matching = [entry for entry in entries if entry.split("\0", 1)[0] == f"worktree {workspace}"]
    if len(matching) != 1 or any(field.startswith(("locked", "prunable")) for field in matching[0].split("\0")):
        raise WorktreeRetirementRefused("worktree_registration_unavailable")
    if _git_run(workspace, "rev-parse", "--show-superproject-working-tree"):
        raise WorktreeRetirementRefused("submodule_worktree_unsupported")
    if any(item.startswith("160000 ") for item in _git_run(workspace, "ls-files", "--stage", "-z").split("\0")):
        raise WorktreeRetirementRefused("submodule_worktree_unsupported")
    store = Path(control_store)
    ledger_path = None
    ledger_file = None
    if ledger is not None:
        ledger_identity = WorkspaceIdentity(repository, str(source), str(workspace), str(Path(ledger).absolute()),
                                            slot_id).canonical()
        ledger_path = ledger_identity.ledger
        ledger_file = _file_identity(ledger_path)
    identity = RetirementIdentity(str(store), _file_identity(store), binding.repository, str(source),
                                  _directory_identity(source), str(common), _directory_identity(common),
                                  str(workspace), physical, str(admin), _directory_identity(admin),
                                  _git_run(workspace, "rev-parse", "--verify", "HEAD^{commit}"),
                                  ledger_path, slot_id, ledger_file)
    workspace_identity = identity.workspace_identity()
    row = PathLifecycle(store).snapshot(workspace_identity)
    if row is not None and row["operation"] != workspace_identity.encoded():
        raise WorktreeRetirementRefused("exact_lifecycle_identity_required")
    return identity


def verify_reference(reference, identity, *, owner=False):
    """Pin operator-supplied recovery/adoption evidence; this does not create authority.

    The trusted operator is responsible for the evidence's truth and selected
    producer adoption. Neither a digest nor a source timestamp proves quiescence.
    """
    path, digest = reference
    _file_identity(path)
    data = Path(path).read_bytes()
    if sha256(data).hexdigest() != digest:
        raise WorktreeRetirementRefused("evidence_reference_changed")
    try:
        evidence = json.loads(data)
        expected = json.loads(json.dumps(asdict(identity)))
        if evidence["identity"] != expected:
            raise ValueError
        if owner and (not isinstance(evidence.get("owner"), str) or not evidence["owner"]
                      or len(evidence.get("producer_generation", "")) != 40
                      or any(c not in "0123456789abcdef" for c in evidence["producer_generation"])):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise WorktreeRetirementRefused("exact_evidence_identity_required") from None
    return str(Path(path)), digest
