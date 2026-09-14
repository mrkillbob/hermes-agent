"""Explicit local Git operations used by the stack lifecycle."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .stack import _branch


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
        argv = ("git", "-C", str(self.repository), *args)
        return self._run_argv(argv)

    def _run_argv(self, argv: tuple[str, ...]) -> GitEvidence:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env=self._environment,
            cwd=str(self.repository),
        )
        evidence = GitEvidence(argv, result.returncode, result.stdout, result.stderr)
        if result.returncode:
            raise GitStackError(result.stderr.strip() or "git command failed")
        return evidence

    def native_stack_checkout(self, selector: str) -> GitEvidence:
        """Check out a GitHub-native stack by PR number or stack number."""

        if not selector or not selector.isdigit() or int(selector) < 1:
            raise GitStackError("native stack selector is invalid")
        # gh-stack records its imported stack in the worktree's linked Git
        # metadata. Read that exact local number first; this avoids asking
        # the extension to rediscover an already-imported stack through a
        # separate identity/config context.
        local_number = self._local_native_stack_number()
        if local_number == int(selector):
            return GitEvidence(("gh", "stack", "view"), 0, f"Stack #{selector}\n", "")
        try:
            view = self._run_argv(("gh", "stack", "view"))
        except GitStackError:
            view = None
        if view is not None and f"Stack #{selector}" in view.stdout:
            return view
        return self._run_argv(("gh", "stack", "checkout", selector))

    def _local_native_stack_number(self) -> int | None:
        """Read the exact stack number from linked Git metadata, if present."""

        try:
            location = self._run("rev-parse", "--git-path", "gh-stack").stdout.strip()
            path = Path(location)
            if not path.is_absolute():
                path = self.repository / path
            payload = json.loads(path.read_text(encoding="utf-8"))
            stacks = payload.get("stacks") if isinstance(payload, dict) else None
            if not isinstance(stacks, list) or len(stacks) != 1 or not isinstance(stacks[0], dict):
                return None
            number = stacks[0].get("number")
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                return None
            return number
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def native_stack_rebase(self) -> GitEvidence:
        """Cascade-rebase the checked-out native stack, stopping on conflict."""

        return self._run_argv(("gh", "stack", "rebase"))

    def native_stack_push(self) -> GitEvidence:
        """Push the rebased native stack using gh-stack's lease checks."""

        return self._run_argv(("gh", "stack", "push"))

    def branch_head(self, branch: str) -> str:
        _branch(branch, "branch")
        return self._run("rev-parse", f"refs/remotes/origin/{branch}").stdout.strip()

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
