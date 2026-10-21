"""Git command boundary shared by receipt worktree materializers."""
from dataclasses import dataclass
from typing import Protocol
import subprocess


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


class GitCommandRunner(Protocol):
    def run(self, argv: list[str]) -> GitCommandResult: ...


class SubprocessGitRunner:
    def run(self, argv: list[str]) -> GitCommandResult:
        from .caller_repository import reject_git_overrides
        reject_git_overrides()
        try:
            completed = subprocess.run(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("local Git verification failed") from error
        return GitCommandResult(completed.returncode, completed.stdout, completed.stderr)
