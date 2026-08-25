from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess

from github_pr_feedback.controller import PreparedWorktree
from github_pr_feedback.github_client import (
    CheckState,
    PullRequestMergeState,
    ReviewState,
)
from github_pr_feedback.ledger import FeedbackLedger
from github_pr_feedback.policy import load_policy
from github_pr_feedback.repair_controller import RepairController, repair_triggers


SHA = "a" * 40


def merge_state(
    *, mergeable: bool = True, status: str = "CLEAN"
) -> PullRequestMergeState:
    return PullRequestMergeState(
        repository="acme/widgets",
        number=17,
        state="OPEN",
        is_draft=False,
        mergeable=mergeable,
        merge_state_status=status,
        base_branch="main",
        base_sha="b" * 40,
        head_repository="acme/widgets",
        author_login="owner",
        head_ref_name="codex/fix",
        head_sha=SHA,
        merged=False,
        merge_commit_oid=None,
    )


def policy(tmp_path: Path, *, report_only: bool = False):
    repository = tmp_path / "repo"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    return load_policy({
        "enabled": True,
        "repositories": [
            {
                "base_repository": "acme/widgets",
                "head_repository": "acme/widgets",
                "local_path": str(repository),
                "owner_login": "owner",
                "branch_prefixes": ["codex/"],
            }
        ],
        "reviewer_logins": ["reviewer"],
        "reviewer_associations": [],
        "not_before": "2026-08-25T00:00:00Z",
        "assignee": "fallback",
        "board": "repairs",
        "repair_steward": {
            "enabled": True,
            "assignee": "pr-repair-steward",
            "repositories": ["acme/widgets"],
            "report_only": report_only,
        },
    })


def test_repair_triggers_cover_conflicts_changes_requested_and_non_green_actions() -> (
    None
):
    assert repair_triggers(
        merge_state(mergeable=False, status="DIRTY"),
        ReviewState("CHANGES_REQUESTED", 1),
        CheckState(True, False, 2),
    ) == ("merge_conflict", "changes_requested", "actions_not_green")


class GitHub:
    def list_open_pull_requests(self, repository: str, owner: str):
        from github_pr_feedback.policy import PullRequest

        return (
            PullRequest(17, "OPEN", repository, repository, owner, "codex/fix", SHA),
        )

    def get_merge_state(self, repository: str, number: int):
        return merge_state(mergeable=False, status="DIRTY")

    def get_review_state(self, repository: str, number: int):
        return ReviewState(None, 0)

    def get_check_state(self, repository: str, head_sha: str):
        return CheckState(False, True, 0)


class LocalGit:
    def prepare_receipt_worktree(self, path: Path, receipt):
        return PreparedWorktree(path / "exact", "hermes/repair", receipt.head_sha)


class Kanban:
    def __init__(self):
        self.tasks = []

    def create_or_get_task(self, task):
        self.tasks.append(task)
        return "repair-task"


def test_repair_controller_dedupes_exact_head_and_preserves_merge_authority(
    tmp_path: Path,
) -> None:
    configured = policy(tmp_path)
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    kanban = Kanban()
    controller = RepairController(
        configured,
        ledger,
        GitHub(),
        kanban,
        LocalGit(),
        clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )

    first = controller.scan()
    second = controller.scan()

    assert first.created == 1
    assert second.created == 0
    task = kanban.tasks[0]
    assert task.assignee == "pr-repair-steward"
    assert task.initial_status == "running"
    assert "normal merge" in task.instructions
    assert "Do not merge the pull request" in task.instructions
    assert "Do not force-push" in task.instructions
    assert "Do not weaken" in task.instructions
    ledger.close()


def test_report_only_repair_scan_creates_a_blocked_observation(tmp_path: Path) -> None:
    configured = policy(tmp_path, report_only=True)
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    kanban = Kanban()

    result = RepairController(configured, ledger, GitHub(), kanban, LocalGit()).scan()

    assert result.created == 1
    assert kanban.tasks[0].initial_status == "blocked"
    assert "Report only" in kanban.tasks[0].instructions
    ledger.close()


def test_report_only_receipt_does_not_block_later_active_repair(tmp_path: Path) -> None:
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    kanban = Kanban()
    kwargs = {
        "ledger": ledger,
        "github": GitHub(),
        "kanban": kanban,
        "local_git": LocalGit(),
        "clock": lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    }

    report = RepairController(policy(tmp_path, report_only=True), **kwargs).scan()
    active = RepairController(policy(tmp_path, report_only=False), **kwargs).scan()

    assert report.created == 1
    assert active.created == 1
    assert [task.initial_status for task in kanban.tasks] == ["blocked", "running"]
    ledger.close()
