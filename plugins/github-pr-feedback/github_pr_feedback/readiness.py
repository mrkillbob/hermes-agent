"""Pure readiness and queue ordering rules for the merge-maintainer handoff."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    pull_request_open: bool
    exact_head: bool
    mergeable: bool
    feedback_clear: bool
    local_ci_passed: bool
    codex_reviewed: bool
    codex_clean: bool
    no_pending_repairs: bool
    no_pending_intent: bool

    def with_changes(self, **changes: object) -> "ReadinessEvidence":
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class ReadinessDecision:
    eligible: bool
    blockers: tuple[str, ...]


def evaluate_readiness(evidence: ReadinessEvidence) -> ReadinessDecision:
    checks = (
        ("pull_request_not_open", evidence.pull_request_open),
        ("exact_head_unverified", evidence.exact_head),
        ("mergeability_unavailable", evidence.mergeable),
        ("feedback_not_clear", evidence.feedback_clear),
        ("local_ci_not_passing", evidence.local_ci_passed),
        ("codex_review_pending", evidence.codex_reviewed),
        ("pending_repair", evidence.no_pending_repairs),
        ("pending_intent", evidence.no_pending_intent),
    )
    blockers = tuple(name for name, passed in checks if not passed)
    return ReadinessDecision(not blockers, blockers)


@dataclass(frozen=True, slots=True)
class ReadyPullRequest:
    repository: str
    number: int
    head_sha: str
    ready_at: int
    codex_clean: bool
    risk_rank: int
    changed_files: int


def order_ready_queue(items: list[ReadyPullRequest] | tuple[ReadyPullRequest, ...]) -> tuple[ReadyPullRequest, ...]:
    """Prefer Codex-clean heads, then oldest and lowest-risk work."""

    return tuple(sorted(items, key=lambda item: (
        not item.codex_clean, item.ready_at, item.risk_rank,
        item.changed_files, item.repository.casefold(), item.number,
    )))
