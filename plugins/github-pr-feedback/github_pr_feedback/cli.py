"""Hermes CLI wiring for governed GitHub pull-request feedback intake."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from .controller import KanbanTask, ScanController
from .github_client import GitHubClient
from .ledger import FeedbackLedger
from .policy import FeedbackReceipt, PluginPolicy, load_policy

try:
    from hermes_constants import get_default_hermes_root
except ImportError:
    def get_default_hermes_root() -> Path:
        configured = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        return configured.parent.parent if configured.parent.name == "profiles" else configured

_MISSING = object()


@dataclass(frozen=True, slots=True)
class KanbanCommandResult:
    returncode: int
    stdout: str


@dataclass(frozen=True, slots=True)
class DoctorCommandResult:
    returncode: int
    stdout: str


class DoctorCommandRunner(Protocol):
    def which(self, executable: str) -> str | None: ...

    def run(self, argv: list[str]) -> DoctorCommandResult: ...


class SubprocessDoctorRunner:
    def which(self, executable: str) -> str | None:
        resolved = shutil.which(executable)
        if not resolved:
            return None
        path = Path(resolved).resolve()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            return None
        return str(path)

    def run(self, argv: list[str]) -> DoctorCommandResult:
        try:
            completed = subprocess.run(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return DoctorCommandResult(1, "")
        return DoctorCommandResult(completed.returncode, completed.stdout)


class DoctorProbe:
    """Read-only readiness probes with an injected external-command boundary."""

    def __init__(self, hermes_root: Path, runner: DoctorCommandRunner | None = None) -> None:
        self._hermes_root = Path(hermes_root)
        self._runner = runner or SubprocessDoctorRunner()

    def checks(self, policy: PluginPolicy, ledger_path: Path) -> dict[str, str]:
        gh = self._runner.which("gh")
        hermes = self._runner.which("hermes")
        checks = {
            "gh_executable": gh is not None,
            "gh_auth": bool(
                gh
                and self._command_ok([gh, "auth", "status", "--hostname", "github.com"])
            ),
            "hermes_executable": bool(
                hermes and self._command_ok([hermes, "--version"])
            ),
            "board": self._board_exists(policy.board or ""),
            "assignee": all(
                self._assignee_exists(assignee)
                for assignee in {
                    policy.assignee or "",
                    *(rule.assignee for rule in policy.assignee_rules),
                    *(
                        [policy.local_ci_audit.assignee]
                        if policy.local_ci_audit is not None
                        else []
                    ),
                }
            ),
            "ledger_access": self._ledger_access(ledger_path),
            "repository_worktree": self._repositories_ready(
                tuple(target.local_path for target in policy.targets.values()),
                ledger_path.parent / "worktrees",
            ),
        }
        return {name: "ok" if ok else "failed" for name, ok in checks.items()}

    def _command_ok(self, argv: list[str]) -> bool:
        try:
            return self._runner.run(argv).returncode == 0
        except Exception:  # noqa: BLE001 - doctor reports failure and continues.
            return False

    def _board_exists(self, board: str) -> bool:
        if not _safe_name(board):
            return False
        if board == "default":
            return self._hermes_root.is_dir()
        directory = self._hermes_root / "kanban" / "boards" / board
        return (directory / "board.json").is_file() or (directory / "kanban.db").is_file()

    def _assignee_exists(self, assignee: str) -> bool:
        if not _safe_name(assignee):
            return False
        if assignee == "default":
            return self._hermes_root.is_dir()
        return (self._hermes_root / "profiles" / assignee / "config.yaml").is_file()

    def _ledger_access(self, path: Path) -> bool:
        try:
            if path.exists():
                if not path.is_file():
                    return False
                with path.open("rb") as handle:
                    header = handle.read(16)
                return header == b"SQLite format 3\x00"
            return _nearest_existing_parent_access(path.parent)
        except OSError:
            return False

    def _repositories_ready(self, repositories: tuple[Path, ...], worktree_root: Path) -> bool:
        git = self._runner.which("git")
        all_ready = git is not None and _nearest_existing_parent_access(worktree_root)
        if git is None:
            return False
        for repository in repositories:
            try:
                top = self._runner.run(
                    [git, "-C", str(repository), "rev-parse", "--show-toplevel"]
                )
                common = self._runner.run(
                    [git, "-C", str(repository), "rev-parse", "--git-common-dir"]
                )
                worktrees = self._runner.run(
                    [git, "-C", str(repository), "worktree", "list", "--porcelain"]
                )
                repository_ready = (
                    top.returncode == 0
                    and Path(top.stdout.strip()).resolve() == repository.resolve()
                    and common.returncode == 0
                    and bool(common.stdout.strip())
                    and worktrees.returncode == 0
                )
            except (OSError, RuntimeError, ValueError):
                repository_ready = False
            all_ready = all_ready and repository_ready
        return all_ready


class KanbanCommandRunner(Protocol):
    def run(self, argv: list[str]) -> KanbanCommandResult: ...


class SubprocessKanbanRunner:
    """Run the fixed Hermes Kanban command without a shell."""

    def run(self, argv: list[str]) -> KanbanCommandResult:
        try:
            completed = subprocess.run(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("Kanban task creation failed") from error
        return KanbanCommandResult(completed.returncode, completed.stdout)


class KanbanSubprocessClient:
    """Kanban adapter that accepts only a valid JSON task identity."""

    def __init__(self, runner: KanbanCommandRunner | None = None) -> None:
        self._runner = runner or SubprocessKanbanRunner()

    def create_or_get_task(self, task: KanbanTask) -> str:
        result = self._runner.run(_kanban_create_argv(task))
        if result.returncode != 0:
            raise RuntimeError("Kanban task creation failed")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Kanban task creation failed") from error
        task_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(task_id, str) or not task_id.strip():
            raise RuntimeError("Kanban task creation failed")
        return task_id.strip()


def _kanban_create_argv(task: KanbanTask) -> list[str]:
    body = (
        f"{task.instructions}\n\n{task.evidence_heading}:\n"
        f"{json.dumps(task.evidence, sort_keys=True)}"
    )
    return [
        "hermes",
        "kanban",
        "--board",
        task.board,
        "create",
        task.title,
        "--body",
        body,
        "--assignee",
        task.assignee,
        "--workspace",
        f"dir:{task.repository_path}",
        "--idempotency-key",
        task.idempotency_key,
        "--max-retries",
        str(task.max_retries),
        "--initial-status",
        task.initial_status,
        "--json",
    ]


def setup_cli(_ctx: Any, parser: argparse.ArgumentParser) -> None:
    """Attach the plugin's command tree to the host-created parser."""

    subcommands = parser.add_subparsers(dest="github_pr_feedback_action", required=True)
    subcommands.add_parser("scan", help="Read and dispatch newly admitted feedback")
    subcommands.add_parser("status", help="Show durable receipt counts")
    subcommands.add_parser("doctor", help="Check configuration readiness without scanning")
    retry = subcommands.add_parser("retry", help="Retry one failed, immutable feedback receipt")
    retry.add_argument("--repository", required=True)
    retry.add_argument("--pr-number", required=True, type=int)
    retry.add_argument("--feedback-kind", required=True)
    retry.add_argument("--feedback-id", required=True)
    retry.add_argument("--head-sha", required=True)


def handle_cli_with_context(ctx: Any, args: argparse.Namespace) -> int:
    action = getattr(args, "github_pr_feedback_action", None)
    if action == "scan":
        return _scan(ctx)
    if action == "status":
        return _status()
    if action == "doctor":
        return _doctor(ctx)
    if action == "retry":
        return _retry(ctx, args)
    return 2


def cli_bindings(ctx: Any) -> tuple[Any, Any]:
    """Bind host-supplied settings access without reading global YAML."""

    return partial(setup_cli, ctx), partial(handle_cli_with_context, ctx)


def _scan(ctx: Any) -> int:
    try:
        policy = _load_policy_from_context(ctx)
    except ValueError:
        print(json.dumps({"status": "invalid_configuration"}, sort_keys=True))
        return 1
    ledger = FeedbackLedger.for_current_profile()
    try:
        result = _controller(policy, ledger).scan()
    finally:
        ledger.close()
    print(json.dumps(_scan_payload(result), sort_keys=True))
    return 1 if result.degraded else 0


def _retry(ctx: Any, args: argparse.Namespace) -> int:
    try:
        policy = _load_policy_from_context(ctx)
        receipt = FeedbackReceipt(
            args.repository,
            args.pr_number,
            args.feedback_kind,
            args.feedback_id,
            args.head_sha,
        )
    except ValueError:
        print(json.dumps({"status": "invalid_configuration"}, sort_keys=True))
        return 1
    ledger = FeedbackLedger.for_current_profile()
    try:
        result = _controller(policy, ledger).retry_failed(receipt)
    finally:
        ledger.close()
    print(json.dumps(_scan_payload(result), sort_keys=True))
    return 1 if result.degraded else 0


def _controller(policy: PluginPolicy, ledger: FeedbackLedger) -> ScanController:
    return ScanController(policy, ledger, GitHubClient(), KanbanSubprocessClient())


def _scan_payload(result) -> dict[str, object]:
    return {
        "status": "degraded" if result.degraded else "ok",
        "created": result.created,
        "skipped": result.skipped,
    }


def _status() -> int:
    ledger = FeedbackLedger.for_current_profile()
    try:
        counts = ledger.status_counts()
    finally:
        ledger.close()
    print(json.dumps(counts, sort_keys=True))
    return 0


def _doctor(
    ctx: Any,
    *,
    probe: DoctorProbe | None = None,
    ledger_path: Path | None = None,
) -> int:
    try:
        policy = _load_policy_from_context(ctx)
    except ValueError:
        print(json.dumps({"status": "invalid_configuration"}, sort_keys=True))
        return 1
    if not policy.enabled:
        print(json.dumps({"status": "disabled"}, sort_keys=True))
        return 0
    path = ledger_path or FeedbackLedger.current_profile_path()
    checks = (probe or DoctorProbe(get_default_hermes_root())).checks(policy, path)
    ready = all(value == "ok" for value in checks.values())
    print(
        json.dumps(
            {"status": "ready" if ready else "degraded", "checks": checks},
            sort_keys=True,
        )
    )
    return 0 if ready else 1


def _load_policy_from_context(ctx: Any) -> PluginPolicy:
    enabled = ctx.get_config("enabled", default=False)
    if enabled is not True:
        return load_policy({"enabled": False})
    settings: dict[str, object] = {"enabled": True}
    for key in (
        "repositories",
        "reviewer_logins",
        "reviewer_associations",
        "include_self_feedback",
        "include_bot_feedback",
        "auto_dispatch",
        "assignee_rules",
        "local_ci_audit",
        "not_before",
        "assignee",
        "board",
    ):
        value = ctx.get_config(key, default=_MISSING)
        if value is not _MISSING:
            settings[key] = value
    return load_policy(settings)


def _safe_name(value: str) -> bool:
    return bool(value) and len(value) <= 64 and all(
        character.isalnum() or character in "-_." for character in value
    ) and "/" not in value and "\\" not in value and value not in {".", ".."}


def _nearest_existing_parent_access(path: Path) -> bool:
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.R_OK | os.W_OK | os.X_OK)
