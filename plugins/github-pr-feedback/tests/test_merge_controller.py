from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from github_pr_feedback.ci_runner import (
    CIAuditIdentity,
    CIAuditReceipt,
    CI_MODE_BUDGET_EXHAUSTED_LOCAL_EQUIVALENT,
)
from github_pr_feedback.github_client import (
    CheckState,
    Feedback,
    GitHubClientError,
    PullRequestMergeState,
    RepositoryMergePolicy,
    ReviewState,
)
from github_pr_feedback.ledger import FeedbackLedger, LedgerStateError
from github_pr_feedback.merge_controller import (
    CanonicalMergeEvidenceSource,
    MergeController,
    MergeSnapshot,
    _codex_reviewed_head,
    evaluate_merge,
)
from github_pr_feedback.policy import MergeMaintainerPolicy, Reviewer, load_policy


BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40
MERGE_SHA = "c" * 40
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def policy(
    *,
    report_only: bool = False,
    allow_budget_exhausted_local_ci: bool = False,
) -> MergeMaintainerPolicy:
    return MergeMaintainerPolicy(
        assignee="pr-merge-maintainer",
        repository="acme/widgets",
        author_login="owner",
        base_branch="stable",
        merge_methods=("squash", "rebase", "merge"),
        receipt_max_age_seconds=3600,
        report_only=report_only,
        post_merge=None,
        allow_budget_exhausted_local_ci=allow_budget_exhausted_local_ci,
    )


def ci_receipt(**overrides: object) -> CIAuditReceipt:
    values: dict[str, object] = {
        "receipt_id": "d" * 64,
        "identity": CIAuditIdentity("acme/widgets", 17, BASE_SHA, HEAD_SHA),
        "manifest_digest": "e" * 64,
        "status": "passed",
        "started_at": NOW - timedelta(minutes=5),
        "completed_at": NOW - timedelta(minutes=4),
        "actions_state": CheckState(False, True, 0),
        "commands": (),
    }
    values.update(overrides)
    return CIAuditReceipt(**values)


def pr_state(**overrides: object) -> PullRequestMergeState:
    values: dict[str, object] = {
        "repository": "acme/widgets",
        "number": 17,
        "state": "OPEN",
        "is_draft": False,
        "mergeable": True,
        "merge_state_status": "CLEAN",
        "base_branch": "stable",
        "base_sha": BASE_SHA,
        "head_repository": "acme/widgets",
        "author_login": "owner",
        "head_ref_name": "codex/fix",
        "head_sha": HEAD_SHA,
        "merged": False,
        "merge_commit_oid": None,
    }
    values.update(overrides)
    return PullRequestMergeState(**values)


def eligible_snapshot(**overrides: object) -> MergeSnapshot:
    values: dict[str, object] = {
        "repository_private": True,
        "pull_request": pr_state(),
        "branch_allowed": True,
        "repository_merge_policy": RepositoryMergePolicy(True, True, True),
        "review_state": ReviewState("APPROVED", 0),
        "check_state": CheckState(False, True, 0),
        "ci_receipt": ci_receipt(),
        "manifest_digest": "e" * 64,
        "feedback_clear": True,
        "base_head_sha": BASE_SHA,
    }
    values.update(overrides)
    return MergeSnapshot(**values)


def enrolled_ledger(tmp_path: Path) -> FeedbackLedger:
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    ledger.enroll_merge_pr(
        "acme/widgets", 17, enrolled_at=NOW, enrolled_by="operator"
    )
    return ledger


@pytest.mark.parametrize(
    "snapshot,blocker",
    [
        (eligible_snapshot(repository_private=False), "repository_not_private"),
        (
            eligible_snapshot(pull_request=pr_state(author_login="stranger")),
            "author_not_allowed",
        ),
        (
            eligible_snapshot(pull_request=pr_state(head_repository="fork/widgets")),
            "head_repository_not_allowed",
        ),
        (eligible_snapshot(pull_request=pr_state(base_branch="main")), "base_branch_mismatch"),
        (eligible_snapshot(branch_allowed=False), "head_branch_not_allowed"),
        (eligible_snapshot(pull_request=pr_state(is_draft=True)), "pull_request_draft"),
        (eligible_snapshot(pull_request=pr_state(mergeable=False)), "pull_request_conflicted"),
        (
            eligible_snapshot(pull_request=pr_state(merge_state_status="BLOCKED")),
            "merge_state_not_clean",
        ),
        (eligible_snapshot(ci_receipt=None), "ci_receipt_missing"),
        (
            eligible_snapshot(ci_receipt=ci_receipt(status="failed")),
            "ci_receipt_not_passing",
        ),
        (
            eligible_snapshot(
                ci_receipt=ci_receipt(completed_at=NOW - timedelta(hours=2))
            ),
            "ci_receipt_stale",
        ),
        (
            eligible_snapshot(ci_receipt=ci_receipt(manifest_digest="f" * 64)),
            "ci_manifest_mismatch",
        ),
        (eligible_snapshot(check_state=CheckState(True, False, 2)), "github_checks_not_green"),
        (
            eligible_snapshot(check_state=CheckState(True, False, 2, False, True)),
            "action_required",
        ),
        (eligible_snapshot(codex_review_pending=True), "codex_review_pending"),
        (
            eligible_snapshot(review_state=ReviewState("CHANGES_REQUESTED", 0)),
            "changes_requested",
        ),
        (
            eligible_snapshot(review_state=ReviewState("APPROVED", 1)),
            "unresolved_review_threads",
        ),
        (eligible_snapshot(feedback_clear=False), "feedback_unprocessed"),
        (eligible_snapshot(intent_review_pending=True), "intent_review_required"),
        (
            eligible_snapshot(repository_merge_policy=RepositoryMergePolicy(False, False, False)),
            "merge_method_unavailable",
        ),
    ],
)
def test_evaluate_merge_fails_closed_with_stable_blocker_codes(
    snapshot: MergeSnapshot, blocker: str
) -> None:
    decision = evaluate_merge(policy(), snapshot, now=NOW)

    assert decision.eligible is False
    assert blocker in decision.blockers
    assert decision.method is None


def test_evaluate_merge_blocks_a_billing_locked_out_check_state_by_default() -> None:

    snapshot = eligible_snapshot(check_state=CheckState(True, False, 2, True))

    decision = evaluate_merge(policy(), snapshot, now=NOW)

    assert decision.eligible is False
    assert "github_actions_budget_exhausted" in decision.blockers


def test_evaluate_merge_accepts_explicit_exact_head_budget_substitution() -> None:
    billing_state = CheckState(True, False, 2, True)
    snapshot = eligible_snapshot(
        check_state=billing_state,
        ci_receipt=ci_receipt(
            actions_state=billing_state,
            ci_mode=CI_MODE_BUDGET_EXHAUSTED_LOCAL_EQUIVALENT,
        ),
    )

    decision = evaluate_merge(
        policy(allow_budget_exhausted_local_ci=True), snapshot, now=NOW
    )

    assert decision.eligible is True
    assert decision.blockers == ()


def test_evaluate_merge_rejects_standard_receipt_as_budget_substitution() -> None:
    snapshot = eligible_snapshot(check_state=CheckState(True, False, 2, True))

    decision = evaluate_merge(
        policy(allow_budget_exhausted_local_ci=True), snapshot, now=NOW
    )

    assert decision.eligible is False
    assert "github_actions_budget_exhausted" in decision.blockers


def test_evaluate_merge_reports_action_required_instead_of_the_generic_not_green_code() -> None:
    """A precise code, not the generic red-X one, so an action_required PR routes
    to human/escalation handling instead of being treated as an ordinary failing
    check a repair or a wait-and-retry could eventually clear."""

    snapshot = eligible_snapshot(check_state=CheckState(True, False, 2, False, True))

    decision = evaluate_merge(policy(), snapshot, now=NOW)

    assert decision.eligible is False
    assert decision.blockers == ("action_required",)


def test_evaluate_merge_selects_first_configured_repository_enabled_method() -> None:
    snapshot = eligible_snapshot(
        repository_merge_policy=RepositoryMergePolicy(False, True, True)
    )

    decision = evaluate_merge(policy(), snapshot, now=NOW)

    assert decision.eligible is True
    assert decision.blockers == ()
    assert decision.method == "rebase"
    assert len(decision.snapshot_digest) == 64


def test_evaluate_merge_rejects_a_receipt_tested_before_the_live_base_head() -> None:
    snapshot = eligible_snapshot(base_head_sha=MERGE_SHA)

    decision = evaluate_merge(policy(), snapshot, now=NOW)

    assert decision.eligible is False
    assert "base_head_changed" in decision.blockers


def test_merge_snapshot_digest_binds_the_live_base_head() -> None:
    current = evaluate_merge(policy(), eligible_snapshot(), now=NOW)
    advanced = evaluate_merge(
        policy(), eligible_snapshot(base_head_sha=MERGE_SHA), now=NOW
    )

    assert current.snapshot_digest != advanced.snapshot_digest


def test_evaluate_merge_requires_review_label_for_governed_risk_or_broad_blast() -> None:
    snapshot = eligible_snapshot(
        pull_request=pr_state(labels=("sweeper:risk-session-state", "sweeper:blast-broad"))
    )

    decision = evaluate_merge(policy(), snapshot, now=NOW)

    assert decision.eligible is False
    assert "governed_review_missing" in decision.blockers


def test_evaluate_merge_accepts_governed_risk_after_explicit_review_label() -> None:
    snapshot = eligible_snapshot(
        pull_request=pr_state(
            labels=("sweeper:risk-session-state", "sweeper:blast-broad", "ci-reviewed")
        )
    )

    decision = evaluate_merge(policy(), snapshot, now=NOW)

    assert decision.eligible is True
    assert "governed_review_missing" not in decision.blockers


class SnapshotSource:
    def __init__(self, snapshots: list[MergeSnapshot]) -> None:
        self.snapshots = snapshots

    def snapshot(self, number: int) -> MergeSnapshot:
        return self.snapshots.pop(0)


class RecordingGitHub:
    def __init__(
        self,
        readbacks: list[PullRequestMergeState | Exception],
        *,
        merge_error: Exception | None = None,
        before_merge_return=None,
    ) -> None:
        self.readbacks = readbacks
        self.merge_error = merge_error
        self.before_merge_return = before_merge_return
        self.merge_calls: list[tuple[str, int, str, str]] = []

    def merge_pull_request(
        self, repository: str, number: int, head_sha: str, *, method: str
    ) -> None:
        self.merge_calls.append((repository, number, head_sha, method))
        if self.before_merge_return is not None:
            self.before_merge_return()
        if self.merge_error is not None:
            raise self.merge_error

    def get_merge_state(self, repository: str, number: int) -> PullRequestMergeState:
        readback = self.readbacks.pop(0)
        if isinstance(readback, Exception):
            raise readback
        return readback


def test_canonical_snapshot_uses_budget_policy_hint_for_exact_head_check_read(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    manifest = repository / "tests/manifests/test_lanes.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version = 1\n", encoding="utf-8")
    plugin_policy = load_policy(
        {
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
            "local_ci_audit": {
                "enabled": True,
                "assignee": "pr-local-ci-auditor",
                "post_results": False,
                "audit_only": True,
                "required_for_open_prs": True,
            },
            "merge_maintainer": {
                "enabled": True,
                "assignee": "pr-merge-maintainer",
                "repository": "acme/widgets",
                "author_login": "owner",
                "base_branch": "stable",
                "merge_methods": ["squash"],
                "receipt_max_age_seconds": 3600,
                "report_only": False,
                "allow_budget_exhausted_local_ci": True,
                "post_merge": {"enabled": False},
            },
        }
    )

    class HintGitHub:
        def __init__(self) -> None:
            self.check_hints: list[bool | None] = []

        def get_merge_state(self, repository: str, number: int):
            return pr_state()

        def list_feedback(self, repository: str, number: int):
            return ()

        def repository_is_private(self, repository: str) -> bool:
            return True

        def get_repository_merge_policy(self, repository: str):
            return RepositoryMergePolicy(True, True, True)

        def get_review_state(self, repository: str, number: int):
            return ReviewState("APPROVED", 0)

        def get_check_state(
            self,
            repository: str,
            head_sha: str,
            *,
            actions_enabled_hint: bool | None = None,
        ):
            self.check_hints.append(actions_enabled_hint)
            return CheckState(True, False, 1, True)

        def get_branch_head(self, repository: str, branch: str) -> str:
            return BASE_SHA

    github = HintGitHub()
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")

    CanonicalMergeEvidenceSource(plugin_policy, github, ledger).snapshot(17)

    assert github.check_hints == [True]
    ledger.close()


def test_merge_controller_rereads_under_lease_and_stops_on_a_race(tmp_path: Path) -> None:
    first = eligible_snapshot()
    raced = replace(first, review_state=ReviewState("CHANGES_REQUESTED", 0))
    github = RecordingGitHub([])
    ledger = enrolled_ledger(tmp_path)
    controller = MergeController(
        policy(), SnapshotSource([first, raced]), github, ledger, owner="test", now=lambda: NOW
    )

    result = controller.run(17)

    assert result.receipt is None
    assert "changes_requested" in result.decision.blockers
    assert github.merge_calls == []
    ledger.close()


def test_prewrite_failed_lease_can_retry_after_the_gate_is_cleared(tmp_path: Path) -> None:
    snapshot = eligible_snapshot()
    raced = replace(snapshot, review_state=ReviewState("CHANGES_REQUESTED", 0))
    github = RecordingGitHub(
        [pr_state(state="CLOSED", merged=True, merge_commit_oid=MERGE_SHA)]
    )
    ledger = enrolled_ledger(tmp_path)
    controller = MergeController(
        policy(),
        SnapshotSource([snapshot, raced, snapshot, snapshot]),
        github,
        ledger,
        owner="test",
        now=lambda: NOW,
    )

    blocked = controller.run(17)
    merged = controller.run(17)

    assert "changes_requested" in blocked.decision.blockers
    assert merged.receipt is not None
    assert len(github.merge_calls) == 1
    ledger.close()


def test_merge_controller_writes_once_and_records_canonical_readback(tmp_path: Path) -> None:
    snapshot = eligible_snapshot(
        repository_merge_policy=RepositoryMergePolicy(False, True, True)
    )
    readback = pr_state(
        state="CLOSED", merged=True, merge_commit_oid=MERGE_SHA, mergeable=True
    )
    github = RecordingGitHub([readback])
    ledger = enrolled_ledger(tmp_path)
    controller = MergeController(
        policy(),
        SnapshotSource([snapshot, snapshot]),
        github,
        ledger,
        owner="test",
        now=lambda: NOW,
    )

    first = controller.run(17)
    second = controller.run(17)

    assert first.receipt is not None
    assert first.receipt.method == "rebase"
    assert first.receipt.merge_commit_oid == MERGE_SHA
    assert second.receipt == first.receipt
    assert github.merge_calls == [("acme/widgets", 17, HEAD_SHA, "rebase")]
    ledger.close()


def test_ambiguous_merge_write_uses_readback_and_never_blindly_retries(tmp_path: Path) -> None:
    snapshot = eligible_snapshot()
    github = RecordingGitHub(
        [pr_state(state="CLOSED", merged=True, merge_commit_oid=MERGE_SHA)],
        merge_error=GitHubClientError("ambiguous transport failure"),
    )
    ledger = enrolled_ledger(tmp_path)
    controller = MergeController(
        policy(),
        SnapshotSource([snapshot, snapshot]),
        github,
        ledger,
        owner="test",
        now=lambda: NOW,
    )

    result = controller.run(17)

    assert result.receipt is not None
    assert result.receipt.merge_commit_oid == MERGE_SHA
    assert len(github.merge_calls) == 1
    ledger.close()


def test_merge_controller_rechecks_enrollment_after_lease_before_write(
    tmp_path: Path,
) -> None:
    snapshot = eligible_snapshot()
    github = RecordingGitHub([])
    ledger = enrolled_ledger(tmp_path)

    class RevokingSource:
        calls = 0

        def snapshot(self, number: int) -> MergeSnapshot:
            self.calls += 1
            if self.calls == 2:
                ledger.unenroll_merge_pr("acme/widgets", number)
            return snapshot

    controller = MergeController(
        policy(), RevokingSource(), github, ledger, owner="test", now=lambda: NOW
    )

    result = controller.run(17)

    assert result.receipt is None
    assert result.decision.blockers == ("merge_pr_not_enrolled",)
    assert github.merge_calls == []
    ledger.close()


def test_merge_write_state_rejects_disable_without_locking_unrelated_writers(
    tmp_path: Path,
) -> None:
    snapshot = eligible_snapshot()
    database = tmp_path / "ledger.sqlite3"
    ledger = enrolled_ledger(tmp_path)
    disable_errors: list[str] = []

    def concurrent_writes() -> None:
        concurrent = FeedbackLedger(database)
        try:
            with pytest.raises(LedgerStateError) as raised:
                concurrent.unenroll_merge_pr("acme/widgets", 17)
            disable_errors.append(str(raised.value))
            concurrent.enroll_merge_pr(
                "other/widgets", 22, enrolled_at=NOW, enrolled_by="operator"
            )
        finally:
            concurrent.close()

    github = RecordingGitHub(
        [pr_state(state="CLOSED", merged=True, merge_commit_oid=MERGE_SHA)],
        before_merge_return=concurrent_writes,
    )
    controller = MergeController(
        policy(), SnapshotSource([snapshot, snapshot]), github, ledger, owner="test", now=lambda: NOW
    )

    result = controller.run(17)

    assert result.receipt is not None
    assert disable_errors == ["merge_in_progress"]
    assert ledger.enrolled_merge_pr_numbers("other/widgets") == (22,)
    ledger.close()


def test_verification_required_attempt_reconciles_canonical_merged_truth_without_rewrite(
    tmp_path: Path,
) -> None:
    snapshot = eligible_snapshot()
    merged_snapshot = eligible_snapshot(
        pull_request=pr_state(
            state="CLOSED",
            merged=True,
            merge_commit_oid=MERGE_SHA,
        )
    )
    github = RecordingGitHub([GitHubClientError("readback unavailable")])
    ledger = enrolled_ledger(tmp_path)
    controller = MergeController(
        policy(),
        SnapshotSource([snapshot, snapshot, merged_snapshot]),
        github,
        ledger,
        owner="test",
        now=lambda: NOW,
    )

    ambiguous = controller.run(17)
    reconciled = controller.run(17)

    assert ambiguous.receipt is None
    assert ambiguous.decision.blockers == ("merge_verification_required",)
    assert reconciled.receipt is not None
    assert reconciled.receipt.tested_head_sha == HEAD_SHA
    assert reconciled.receipt.merge_commit_oid == MERGE_SHA
    assert github.merge_calls == [("acme/widgets", 17, HEAD_SHA, "squash")]
    assert ledger.merge_status_counts() == {
        "claimed": 0,
        "verification_required": 0,
        "completed": 1,
        "failed": 0,
    }
    ledger.close()


def test_report_only_never_acquires_a_write_or_merge_lease(tmp_path: Path) -> None:
    snapshot = eligible_snapshot()
    github = RecordingGitHub([])
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    controller = MergeController(
        policy(report_only=True),
        SnapshotSource([snapshot]),
        github,
        ledger,
        owner="test",
        now=lambda: NOW,
    )

    result = controller.run(17)

    assert result.decision.eligible is True
    assert result.receipt is None
    assert github.merge_calls == []
    assert ledger.merge_status_counts() == {
        "claimed": 0,
        "verification_required": 0,
        "completed": 0,
        "failed": 0,
    }
    ledger.close()


def _codex_summary(status: str, sha: str) -> str:
    return (
        "<!-- codex-pull-request-review-summary -->\n\n## Codex Review Summary\n\n"
        "| Review | Status | Commit | Review trigger |\n| --- | --- | --- | --- |\n"
        f"| Code Review | {status} "
        '<relative-time datetime="2026-08-25T20:00:00Z"></relative-time> | '
        f"`{sha}` | PR opened |"
    )


def _codex_feedback(body: str, *, login: str = "chatgpt-codex-connector[bot]") -> Feedback:
    return Feedback(
        "issue_comment", "1", Reviewer(login, None), body, NOW, True
    )


def test_codex_reviewed_head_true_for_a_completed_review_of_the_exact_head() -> None:
    feedback = (_codex_feedback(_codex_summary("✅ **Completed**", HEAD_SHA[:7])),)

    assert _codex_reviewed_head(feedback, HEAD_SHA) is True


def test_codex_reviewed_head_false_when_no_codex_comment_exists() -> None:
    assert _codex_reviewed_head((), HEAD_SHA) is False


def test_codex_reviewed_head_false_when_the_review_covers_a_different_head() -> None:
    stale_sha = ("f" * 40)[:7]
    feedback = (_codex_feedback(_codex_summary("✅ **Completed**", stale_sha)),)

    assert _codex_reviewed_head(feedback, HEAD_SHA) is False


def test_codex_reviewed_head_false_while_the_review_is_still_running() -> None:
    feedback = (_codex_feedback(_codex_summary("⏳ **Running**", HEAD_SHA[:7])),)

    assert _codex_reviewed_head(feedback, HEAD_SHA) is False


def test_codex_reviewed_head_ignores_a_look_alike_comment_from_another_user() -> None:
    """Only the real Codex App login counts -- anyone can quote the marker text."""

    feedback = (
        _codex_feedback(
            _codex_summary("✅ **Completed**", HEAD_SHA[:7]), login="some-human"
        ),
    )

    assert _codex_reviewed_head(feedback, HEAD_SHA) is False
