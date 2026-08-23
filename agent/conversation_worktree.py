"""Durable, fail-closed Git worktrees for interactive conversation roots."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Iterator

from agent.conversation_worktree_policy import ConversationWorktreePolicy
from hermes_cli._subprocess_compat import noninteractive_git_env
from hermes_state import ConversationWorktreeRecord, SessionDB


logger = logging.getLogger(__name__)

_REPOSITORY_THREAD_LOCKS: dict[str, threading.Lock] = {}
_REPOSITORY_THREAD_LOCKS_GUARD = threading.Lock()


class ConversationWorktreeError(RuntimeError):
    """A conversation worktree could not be safely created or reused."""

    def __init__(self, message: str, *, phase: str) -> None:
        super().__init__(message)
        self.phase = phase


@dataclass(frozen=True)
class ConversationWorktreeBinding:
    """A validated, task-owned working directory for one conversation root."""

    root_session_id: str
    path: Path
    branch: str
    base_commit: str
    repo_common_dir: Path


class ConversationWorktreeManager:
    """Own the Git lifecycle for interactive root-session worktrees only."""

    def __init__(self, policy: ConversationWorktreePolicy, db: SessionDB) -> None:
        self._policy = policy
        self._db = db

    def bind_new_root_session(
        self, root_session_id: str, *, conversation_kind: str
    ) -> ConversationWorktreeBinding | None:
        """Create or recover an interactive root binding without touching source work.

        Task/delegated callers deliberately bypass this manager: they retain their
        existing worktree ownership instead of being converted into conversation
        roots.
        """
        self._event("conversation_worktree.policy", root_session_id=root_session_id)
        if conversation_kind == "task":
            return None
        if conversation_kind != "interactive":
            raise ConversationWorktreeError(
                "conversation kind must be 'interactive' or 'task'", phase="policy"
            )
        if not self._policy.enabled:
            return None
        if not root_session_id:
            raise ConversationWorktreeError(
                "root session id must be non-empty", phase="identity"
            )

        existing = self._db.get_conversation_worktree(root_session_id)
        if existing is not None and existing.state == "ready":
            return self._validated_ready_binding(existing)

        source, source_common_dir = self._source_repository_identity()
        path, branch = self._expected_identity(root_session_id)
        if self._is_within(path, source):
            raise ConversationWorktreeError(
                "worktree_root must not create conversation worktrees inside source_worktree",
                phase="policy",
            )

        if existing is None:
            base_commit = self._git_stdout(source, ["rev-parse", "HEAD"], "identity")
            record = self._db.claim_conversation_worktree(
                root_session_id=root_session_id,
                worktree_path=str(path),
                branch=branch,
                base_commit=base_commit,
                repo_common_dir=str(source_common_dir),
            )
        else:
            record = existing

        try:
            with self._repository_lock(source_common_dir):
                record = self._db.get_conversation_worktree(root_session_id) or record
                self._validate_record_identity(
                    record,
                    path=path,
                    branch=branch,
                    repo_common_dir=source_common_dir,
                )
                if record.state == "ready":
                    return self._validated_ready_binding(record)
                if record.state == "removed":
                    raise ConversationWorktreeError(
                        "conversation worktree was explicitly removed", phase="recovery"
                    )
                if record.state not in {"creating", "creation_failed"}:
                    raise ConversationWorktreeError(
                        f"conversation worktree is not recoverable from state {record.state!r}",
                        phase="recovery",
                    )

                if path.exists():
                    try:
                        self._validate_new_worktree(record)
                    except ConversationWorktreeError as exc:
                        if record.state == "creating":
                            raise ConversationWorktreeError(
                                "conversation worktree path already exists and is not a matching worktree",
                                phase="create",
                            ) from exc
                        raise
                    self._event("conversation_worktree.reuse", root_session_id=root_session_id)
                elif record.state == "creation_failed":
                    raise ConversationWorktreeError(
                        "failed conversation worktree is missing; retained state requires manual recovery",
                        phase="recovery",
                    )
                else:
                    self._create_worktree(source, record)

                self._run_bootstrap(record)
                ready = self._db.mark_conversation_worktree_ready(root_session_id)
                binding = self._binding_from_record(ready)
                self._event("conversation_worktree.ready", root_session_id=root_session_id)
                return binding
        except ConversationWorktreeError as exc:
            self._record_failure(root_session_id, record, exc)
            self._event(
                "conversation_worktree.failure",
                root_session_id=root_session_id,
                phase=exc.phase,
            )
            raise

    def resolve_existing_session(
        self, root_session_id: str
    ) -> ConversationWorktreeBinding | None:
        """Resolve only an already-ready root binding; never create a new one."""
        record = self._db.get_conversation_worktree(root_session_id)
        return self._validated_ready_binding(record) if record is not None else None

    def _source_repository_identity(self) -> tuple[Path, Path]:
        source = self._policy.source_worktree
        if source is None:
            raise ConversationWorktreeError(
                "enabled conversation worktree policy has no source_worktree", phase="policy"
            )
        source = source.resolve()
        if not source.is_dir():
            raise ConversationWorktreeError("source_worktree does not exist", phase="identity")
        common = self._git_stdout(
            source,
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            "identity",
        )
        common_dir = Path(common).resolve()
        if not common_dir.is_dir():
            raise ConversationWorktreeError(
                "source_worktree did not resolve to a usable Git common directory",
                phase="identity",
            )
        return source, common_dir

    def _expected_identity(self, root_session_id: str) -> tuple[Path, str]:
        worktree_root = self._policy.worktree_root
        if worktree_root is None:
            raise ConversationWorktreeError(
                "enabled conversation worktree policy has no worktree_root", phase="policy"
            )
        digest = hashlib.sha256(root_session_id.encode("utf-8")).hexdigest()[:24]
        name = f"conversation-{digest}"
        return worktree_root.resolve() / name, f"{self._policy.branch_prefix}/{name}"

    @staticmethod
    def _is_within(candidate: Path, parent: Path) -> bool:
        try:
            candidate.resolve().relative_to(parent.resolve())
        except ValueError:
            return False
        return True

    def _validate_record_identity(
        self,
        record: ConversationWorktreeRecord,
        *,
        path: Path,
        branch: str,
        repo_common_dir: Path,
    ) -> None:
        if (
            Path(record.worktree_path).resolve() != path.resolve()
            or record.branch != branch
            or Path(record.repo_common_dir).resolve() != repo_common_dir.resolve()
        ):
            raise ConversationWorktreeError(
                "conversation worktree identity conflicts with configured source or root",
                phase="identity",
            )

    @contextmanager
    def _repository_lock(self, common_dir: Path) -> Iterator[None]:
        """Bound one repository's metadata mutation across Hermes processes."""
        lock_path = common_dir / "hermes-conversation-worktree.lock"
        deadline = time.monotonic() + self._policy.create_timeout
        handle = None
        locked = False
        thread_lock = self._thread_lock(common_dir)
        if not thread_lock.acquire(timeout=self._policy.create_timeout):
            raise ConversationWorktreeError(
                "repository worktree lock timed out", phase="create"
            )
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(lock_path, "a+b")
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ConversationWorktreeError(
                            "repository worktree lock timed out", phase="create"
                        )
                    time.sleep(0.05)
            yield
        finally:
            if handle is not None:
                if locked:
                    try:
                        if os.name == "nt":
                            import msvcrt

                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        logger.warning("conversation_worktree.lock_release_failed")
                handle.close()
            thread_lock.release()

    @staticmethod
    def _thread_lock(common_dir: Path) -> threading.Lock:
        """Return the in-process companion to the cross-process file lock."""
        key = str(common_dir.resolve())
        with _REPOSITORY_THREAD_LOCKS_GUARD:
            lock = _REPOSITORY_THREAD_LOCKS.get(key)
            if lock is None:
                lock = threading.Lock()
                _REPOSITORY_THREAD_LOCKS[key] = lock
            return lock

    def _create_worktree(self, source: Path, record: ConversationWorktreeRecord) -> None:
        path = Path(record.worktree_path)
        if path.exists():
            raise ConversationWorktreeError(
                "conversation worktree path already exists", phase="create"
            )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConversationWorktreeError(
                "conversation worktree parent could not be created", phase="create"
            ) from exc

        self._event("conversation_worktree.create", root_session_id=record.root_session_id)
        self._git_stdout(
            source,
            [
                "worktree",
                "add",
                "--no-track",
                "-b",
                record.branch,
                str(path),
                record.base_commit,
            ],
            "create",
        )
        self._validate_new_worktree(record)

    def _validate_new_worktree(self, record: ConversationWorktreeRecord) -> None:
        path = Path(record.worktree_path)
        if not path.is_dir():
            raise ConversationWorktreeError(
                "conversation worktree path is missing", phase="validate"
            )
        head = self._git_stdout(path, ["rev-parse", "HEAD"], "validate")
        branch = self._git_stdout(path, ["branch", "--show-current"], "validate")
        common = Path(
            self._git_stdout(
                path,
                ["rev-parse", "--path-format=absolute", "--git-common-dir"],
                "validate",
            )
        ).resolve()
        if (
            head != record.base_commit
            or branch != record.branch
            or common != Path(record.repo_common_dir).resolve()
        ):
            raise ConversationWorktreeError(
                "created conversation worktree failed identity validation", phase="validate"
            )

    def _validated_ready_binding(
        self, record: ConversationWorktreeRecord
    ) -> ConversationWorktreeBinding:
        if record.state != "ready":
            raise ConversationWorktreeError(
                f"conversation worktree is not ready (state {record.state!r})",
                phase="recovery",
            )
        _, source_common_dir = self._source_repository_identity()
        path, branch = self._expected_identity(record.root_session_id)
        self._validate_record_identity(
            record,
            path=path,
            branch=branch,
            repo_common_dir=source_common_dir,
        )
        if not path.is_dir():
            raise ConversationWorktreeError(
                "ready conversation worktree path is missing", phase="recovery"
            )
        actual_branch = self._git_stdout(path, ["branch", "--show-current"], "recovery")
        actual_common = Path(
            self._git_stdout(
                path,
                ["rev-parse", "--path-format=absolute", "--git-common-dir"],
                "recovery",
            )
        ).resolve()
        if actual_branch != record.branch or actual_common != source_common_dir:
            raise ConversationWorktreeError(
                "ready conversation worktree failed identity validation", phase="recovery"
            )
        ancestor = self._run_git(
            path,
            ["merge-base", "--is-ancestor", record.base_commit, "HEAD"],
            self._policy.create_timeout,
            "recovery",
        )
        if ancestor.returncode != 0:
            raise ConversationWorktreeError(
                "ready conversation worktree no longer descends from its base commit",
                phase="recovery",
            )
        self._event("conversation_worktree.reuse", root_session_id=record.root_session_id)
        return self._binding_from_record(record)

    def _run_bootstrap(self, record: ConversationWorktreeRecord) -> None:
        if not self._policy.bootstrap:
            return
        self._event("conversation_worktree.bootstrap", root_session_id=record.root_session_id)
        try:
            result = subprocess.run(
                list(self._policy.bootstrap_command),
                cwd=record.worktree_path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._policy.bootstrap_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversationWorktreeError(
                f"bootstrap timed out after {self._policy.bootstrap_timeout:g} seconds",
                phase="bootstrap",
            ) from exc
        except OSError as exc:
            raise ConversationWorktreeError(
                "bootstrap command could not start", phase="bootstrap"
            ) from exc
        if result.returncode != 0:
            raise ConversationWorktreeError(
                f"bootstrap command exited with status {result.returncode}",
                phase="bootstrap",
            )

    def _git_stdout(self, cwd: Path, args: list[str], phase: str) -> str:
        result = self._run_git(cwd, args, self._timeout_for_phase(phase), phase)
        if result.returncode != 0:
            raise ConversationWorktreeError(
                f"git {args[0]} failed", phase=phase
            )
        return result.stdout.strip()

    @staticmethod
    def _run_git(
        cwd: Path, args: list[str], timeout: float, phase: str
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(cwd), *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=noninteractive_git_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversationWorktreeError("git command timed out", phase=phase) from exc
        except OSError as exc:
            raise ConversationWorktreeError("git command could not start", phase=phase) from exc

    def _timeout_for_phase(self, phase: str) -> float:
        return self._policy.create_timeout if phase in {"create", "validate", "identity", "recovery"} else self._policy.create_timeout

    def _record_failure(
        self,
        root_session_id: str,
        record: ConversationWorktreeRecord,
        error: ConversationWorktreeError,
    ) -> None:
        if record.state not in {"creating", "creation_failed"}:
            return
        try:
            self._db.mark_conversation_worktree_failed(
                root_session_id,
                failure_phase=error.phase,
                failure_message=str(error)[:500],
            )
        except Exception:
            logger.exception("conversation_worktree.failure_record_failed")

    @staticmethod
    def _binding_from_record(record: ConversationWorktreeRecord) -> ConversationWorktreeBinding:
        return ConversationWorktreeBinding(
            root_session_id=record.root_session_id,
            path=Path(record.worktree_path),
            branch=record.branch,
            base_commit=record.base_commit,
            repo_common_dir=Path(record.repo_common_dir),
        )

    @staticmethod
    def _event(event: str, **fields: str) -> None:
        logger.info(event, extra={"conversation_worktree": fields})
