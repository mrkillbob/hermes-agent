"""Compose governed evidence without granting external mutation authority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from hermes_revenue_lab.ledger.types import parse_timestamp

REQUIRED_SUBSYSTEMS = (
    "model_routing",
    "guard",
    "cron",
    "artifacts",
    "browser",
    "ledger",
    "opportunity_scoring",
)
EXPERIMENT_LANES = ("b2b_opportunity", "niche_intelligence", "digital_product")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is invalid")


def _reference(name: str, value: object) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 1_000:
        raise ValueError(f"{name} is invalid")


@dataclass(frozen=True)
class SubsystemCheck:
    name: str
    passed: bool
    reason_codes: tuple[str, ...]
    source_ref: str

    def __post_init__(self) -> None:
        if self.name not in REQUIRED_SUBSYSTEMS:
            raise ValueError("first-run subsystem is invalid")
        if type(self.passed) is not bool:
            raise ValueError("subsystem status must be a boolean")
        if self.passed and self.reason_codes:
            raise ValueError("passing subsystem cannot contain failure reasons")
        if not self.passed and not self.reason_codes:
            raise ValueError("failed subsystem requires reason codes")
        for reason in self.reason_codes:
            _identifier("subsystem reason", reason)
        _reference("subsystem source reference", self.source_ref)


@dataclass(frozen=True)
class CandidateReceipt:
    candidate_id: str
    experiment_lane: Literal["b2b_opportunity", "niche_intelligence", "digital_product"]
    ranking_tier: Literal["A", "B", "C", "D", "E"]
    scout_eligible: bool
    evidence_ref: str

    def __post_init__(self) -> None:
        _identifier("candidate_id", self.candidate_id)
        if self.experiment_lane not in EXPERIMENT_LANES:
            raise ValueError("experiment lane is invalid")
        if self.ranking_tier not in {"A", "B", "C", "D", "E"}:
            raise ValueError("ranking tier is invalid")
        if type(self.scout_eligible) is not bool:
            raise ValueError("scout eligibility must be a boolean")
        _reference("candidate evidence reference", self.evidence_ref)


@dataclass(frozen=True)
class HumanReviewReceipt:
    review_id: str
    reviewer_kind: str
    authenticated: bool
    reviewed_candidate_ids: tuple[str, ...]
    approved_candidate_ids: tuple[str, ...]
    rationale_refs: tuple[str, ...]
    approval_receipt_ref: str
    reviewed_at: str

    def __post_init__(self) -> None:
        _identifier("review_id", self.review_id)
        if self.reviewer_kind != "human":
            raise ValueError("first-run reviewer must be human")
        if type(self.authenticated) is not bool:
            raise ValueError("review authentication must be a boolean")
        for candidate_id in self.reviewed_candidate_ids + self.approved_candidate_ids:
            _identifier("review candidate", candidate_id)
        for reference in self.rationale_refs:
            _reference("review rationale reference", reference)
        _reference("approval receipt reference", self.approval_receipt_ref)
        parse_timestamp(self.reviewed_at)


@dataclass(frozen=True)
class ExperimentNomination:
    candidate_id: str
    experiment_lane: str
    ranking_tier: str
    evidence_ref: str
    requires_fresh_approval: bool = True
    launch_allowed: bool = False


FirstRunStatus = Literal[
    "blocked_validation",
    "blocked_scout_count",
    "awaiting_human_review",
    "blocked_human_review",
    "ready_for_operator_launch_decision",
]


@dataclass(frozen=True)
class FirstRunPlan:
    status: FirstRunStatus
    reasons: tuple[str, ...]
    candidate_count: int
    nominations: tuple[ExperimentNomination, ...]
    dry_run: bool = True
    publishing_allowed: bool = False
    spending_allowed: bool = False
    customer_outreach_allowed: bool = False
    experiment_launch_allowed: bool = False
    authority: str = "readiness_only"


def _plan(
    status: FirstRunStatus,
    reasons: tuple[str, ...],
    candidate_count: int,
    nominations: tuple[ExperimentNomination, ...] = (),
) -> FirstRunPlan:
    return FirstRunPlan(status, reasons, candidate_count, nominations)


def build_first_run_plan(
    subsystem_checks: tuple[SubsystemCheck, ...],
    candidates: tuple[CandidateReceipt, ...],
    *,
    human_review: HumanReviewReceipt | None,
) -> FirstRunPlan:
    """Validate dry-run readiness and nominate; never launch or mutate externally."""

    names = tuple(item.name for item in subsystem_checks)
    if len(names) != len(set(names)) or set(names) != set(REQUIRED_SUBSYSTEMS):
        raise ValueError("every first-run subsystem must appear exactly once")
    candidate_ids = tuple(item.candidate_id for item in candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("first-run candidate IDs must be unique")

    failed = tuple(
        f"{item.name}:{reason}"
        for item in subsystem_checks
        if not item.passed
        for reason in item.reason_codes
    )
    if failed:
        return _plan("blocked_validation", failed, len(candidates))
    if len(candidates) < 20:
        return _plan(
            "blocked_scout_count",
            ("minimum_20_candidates_not_met",),
            len(candidates),
        )
    if human_review is None:
        return _plan(
            "awaiting_human_review", ("human_review_required",), len(candidates)
        )

    review_reasons: list[str] = []
    reviewed = human_review.reviewed_candidate_ids
    approved = human_review.approved_candidate_ids
    if not human_review.authenticated:
        review_reasons.append("human_review_not_authenticated")
    if len(reviewed) != len(set(reviewed)) or set(reviewed) != set(candidate_ids):
        review_reasons.append("human_review_does_not_cover_candidate_set")
    if len(approved) != 3 or len(approved) != len(set(approved)):
        review_reasons.append("exactly_three_unique_candidates_required")
    if not set(approved).issubset(candidate_ids):
        review_reasons.append("approved_candidate_not_in_scout_set")
    if len(human_review.rationale_refs) != len(approved):
        review_reasons.append("approved_candidate_rationale_missing")

    by_id = {item.candidate_id: item for item in candidates}
    approved_rows = [by_id[item] for item in approved if item in by_id]
    if any(not item.scout_eligible for item in approved_rows):
        review_reasons.append("approved_candidate_not_scout_eligible")
    if {item.experiment_lane for item in approved_rows} != set(EXPERIMENT_LANES):
        review_reasons.append("one_candidate_per_experiment_lane_required")
    if review_reasons:
        return _plan("blocked_human_review", tuple(review_reasons), len(candidates))

    nominations = tuple(
        ExperimentNomination(
            candidate_id=item.candidate_id,
            experiment_lane=item.experiment_lane,
            ranking_tier=item.ranking_tier,
            evidence_ref=item.evidence_ref,
        )
        for item in approved_rows
    )
    return _plan("ready_for_operator_launch_decision", (), len(candidates), nominations)
