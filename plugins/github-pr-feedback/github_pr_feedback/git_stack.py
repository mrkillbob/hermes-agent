"""Explicit local Git operations used by the stack lifecycle."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .stack import _branch


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class GitEvidence:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class GitStackError(RuntimeError):
    pass


class GitStackRunner:
    def __init__(self, repository: Path, *, environment: Mapping[str, str] | None = None) -> None:
        self.repository = Path(repository)
        self._environment = None if environment is None else dict(environment)

    def _run(self, *args: str) -> GitEvidence:
        return self._run_at(self.repository, *args)

    def _run_at(self, repository: Path, *args: str) -> GitEvidence:
        argv = ("git", "-C", str(repository), *args)
        result = subprocess.run(
            argv, check=False, capture_output=True, text=True, env=self._environment
        )
        evidence = GitEvidence(argv, result.returncode, result.stdout, result.stderr)
        if result.returncode:
            raise GitStackError(result.stderr.strip() or "git command failed")
        return evidence

    def branch_head(self, branch: str) -> str:
        _branch(branch, "branch")
        return self._run("rev-parse", f"refs/remotes/origin/{branch}").stdout.strip()

    def head_sha(self) -> str:
        """Return the full commit ID currently checked out in the worktree."""

        head = self._run("rev-parse", "HEAD").stdout.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}", head):
            raise GitStackError("local HEAD was not a full Git object ID")
        return head

    def merge_base_into_branch(self, branch: str, base_branch: str) -> GitEvidence:
        _branch(branch, "branch")
        _branch(base_branch, "base_branch")
        self._run("fetch", "origin", base_branch, branch)
        self._run("switch", branch)
        return self._run("merge", "--no-edit", "--no-ff", f"origin/{base_branch}")

    def push_branch(self, branch: str) -> GitEvidence:
        _branch(branch, "branch")
        return self._run(
            "push",
            "origin",
            f"HEAD:refs/heads/{branch}",
        )

    def push_verified_head(
        self, repository: str, branch: str, expected_head_sha: str
    ) -> GitEvidence:
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("repository must be an owner/repository name")
        _branch(branch, "branch")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", expected_head_sha):
            raise ValueError("expected_head_sha must be a full Git object ID")
        local_head = self.head_sha()
        if local_head.casefold() == expected_head_sha.casefold():
            raise GitStackError("local HEAD does not contain a repair commit")
        self._run("merge-base", "--is-ancestor", expected_head_sha, "HEAD")
        objects = Path(self._run("rev-parse", "--git-path", "objects").stdout.strip())
        with tempfile.TemporaryDirectory(prefix="hermes-git-push-") as temporary:
            isolated = Path(temporary)
            self._run_at(isolated, "init", "--bare", "--quiet")
            (isolated / "objects" / "info" / "alternates").write_text(
                f"{objects}\n", encoding="utf-8"
            )
            self._run_at(isolated, "update-ref", "refs/heads/hermes-push", local_head)
            return self._run_at(
                isolated,
                "push",
                f"https://github.com/{repository}.git",
                f"--force-with-lease=refs/heads/{branch}:{expected_head_sha}",
                "refs/heads/hermes-push:refs/heads/" + branch,
            )
