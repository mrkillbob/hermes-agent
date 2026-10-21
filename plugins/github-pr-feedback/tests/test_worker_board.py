"""Worker board ownership across caller-bound typed CI successor dispatch."""
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from github_pr_feedback.ci_runner import CIAuditIdentity, CIAuditReceipt, CommandEvidence
from github_pr_feedback.controller import ScanController
from github_pr_feedback.github_client import CheckState
from github_pr_feedback.ledger import FeedbackLedger
from github_pr_feedback.policy import FeedbackReceipt, PullRequest
from test_scan_controller import FakeGitHub, RecordingKanban, RecordingLocalGit, initialized_repository


@pytest.mark.parametrize(
    "actions_state",
    [
        pytest.param(CheckState(True, False, 0), id="zero-checks"),
        pytest.param(CheckState(True, False, 1, billing_blocked=True), id="billing-blocked"),
        pytest.param(CheckState(True, False, 1), id="non-green-check"),
    ],
)
def test_failed_exact_head_static_receipt_immediately_dispatches_one_typed_fixer(
    tmp_path: Path,
    actions_state: CheckState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from github_pr_feedback.caller_repository import CallerRepository, ScopedContext, repository_scope
    from github_pr_feedback.cli import _load_policy_from_context
    local_path, head_sha = initialized_repository(tmp_path)
    subprocess.run(["git", "-C", str(local_path), "remote", "add", "origin",
                    "https://github.com/acme/widgets.git"], check=True)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "worker-repairs")
    base_sha = "b" * 40
    raw = {
        "enabled": True,
        "repositories": [
            {
                "base_repository": "acme/widgets",
                "head_repository": "acme/widgets",
                "local_path": str(local_path),
                "owner_login": "owner",
                "branch_prefixes": ["codex/"],
            }
        ],
        "reviewer_logins": ["reviewer"],
        "reviewer_associations": [],
        "not_before": "2026-08-24T00:00:00Z",
        "assignee": "task-orchestrator",
        "routing_rules": [
            {
                "assignee": "ci-static-fixer",
                "precedence": 150,
                "match_any": ["static lane"],
                "match_labels_any": ["ci/static"],
                "tags": ["type/ci", "ci/static"],
                "priority": "P2",
                "blast_radius": "contained",
                "risks": [],
                "requires_review": False,
            }
        ],
        "auto_dispatch": True,
        "board": "repairs",
        "local_ci_audit": {
            "enabled": True,
            "assignee": "pr-local-ci-auditor",
            "post_results": True,
        },
    }
    current = PullRequest(
        17,
        "OPEN",
        "acme/widgets",
        "acme/widgets",
        "owner",
        "codex/fix",
        head_sha,
        base_branch="main",
        base_sha=base_sha,
    )
    github = FakeGitHub(current, ())
    kanban = RecordingKanban()
    local_git = RecordingLocalGit()
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    receipt = CIAuditReceipt(
        receipt_id="f" * 64,
        identity=CIAuditIdentity("acme/widgets", 17, base_sha, head_sha),
        manifest_digest="e" * 64,
        status="failed",
        started_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 8, 25, 12, 1, tzinfo=UTC),
        actions_state=actions_state,
        commands=(
            CommandEvidence(
                argv=(".venv/bin/python", "scripts/run_static_lane.py"),
                cwd=".",
                returncode=1,
                duration_ms=1,
                timed_out=False,
                stdout_sha256="0" * 64,
                stderr_sha256="0" * 64,
                classification="logic-regression",
            ),
        ),
    )
    ledger.record_ci_receipt(receipt)
    unrelated_repair = FeedbackReceipt(
        "acme/widgets", 17, "pr_repair", "actions-not-green", head_sha
    )
    unrelated_lease = ledger.claim(
        unrelated_repair,
        owner="existing-repair",
        claimed_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        stale_before=datetime(2026, 8, 25, 11, 55, tzinfo=UTC),
    )
    assert unrelated_lease is not None
    ctx = SimpleNamespace(session_cwd=lambda **_kwargs: str(local_path),
                          get_config=lambda key, default=None: raw.get(key, default))
    scoped = ScopedContext(ctx, CallerRepository.resolve(ctx))
    with repository_scope(scoped.binding):
        controller = ScanController(
            _load_policy_from_context(scoped), ledger, github, kanban, local_git,
            control_home=tmp_path,
        )
        assert controller.dispatch_ci_failure(receipt) == "scheduled"
        assert controller.dispatch_ci_failure(receipt) == "duplicate"
    assert len(kanban.tasks) == 1
    task = kanban.tasks[0]
    assert task.board == "worker-repairs"
    assert kanban.promoted == [("worker-repairs", "kanban-1")]
    assert task.evidence["repository"] == "acme/widgets"
    assert task.assignee == "ci-static-fixer"
    assert task.head_sha == head_sha
    assert task.initial_status == "blocked"
    assert task.max_runtime_seconds == 60 * 60
    assert task.max_retries == 2
    assert task.idempotency_key.endswith(":typed-fixer-v3")
    assert "first 90 seconds" in task.instructions
    assert "do not repeat completed work" in task.instructions
    assert task.evidence["ci_receipt_id"] == "f" * 64
    assert task.evidence["failed_command"]["classification"] == "logic-regression"
    assert "pr-maintenance-receipt:v1" in task.instructions
    assert local_git.calls[0][1].feedback_kind == "pr_repair"
    ledger.close()

