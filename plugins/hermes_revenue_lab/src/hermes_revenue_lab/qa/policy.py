"""HRL-11 fail-closed deliverable validation and publish eligibility."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from hermes_revenue_lab.approvals import ApprovalDecision, ApprovalStatus
from hermes_revenue_lab.compliance import ComplianceDecision, DecisionStatus
from hermes_revenue_lab.ledger.types import parse_timestamp
from hermes_revenue_lab.routing.types import TIER_NAMES, TierName

QA_DIMENSIONS = (
    "factual_claims",
    "links",
    "calculations",
    "duplicated_material",
    "copyright_concerns",
    "source_attribution",
    "hallucinations",
    "formatting",
    "customer_usefulness",
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MODEL_DIGEST = re.compile(r"[0-9a-f]{12,64}")
_ARTIFACT_TYPES = {
    "report",
    "calculator",
    "spreadsheet",
    "checklist",
    "reference",
    "utility",
}


def _identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is invalid")


def _sha256(name: str, value: object) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} artifact hash is invalid")


@dataclass(frozen=True)
class Deliverable:
    artifact_id: str
    artifact_sha256: str
    artifact_type: str
    value_class: Literal["low", "high"]
    target_platform: str
    generator_model: str
    generator_digest: str
    generator_provider: str
    generator_context_id: str
    generated_at: str

    def __post_init__(self) -> None:
        _identifier("artifact_id", self.artifact_id)
        _sha256("deliverable", self.artifact_sha256)
        if self.artifact_type not in _ARTIFACT_TYPES:
            raise ValueError("deliverable artifact type is invalid")
        if self.value_class not in {"low", "high"}:
            raise ValueError("deliverable value class is invalid")
        _identifier("target_platform", self.target_platform)
        for name in ("generator_model", "generator_provider", "generator_context_id"):
            _identifier(name, getattr(self, name))
        if not _MODEL_DIGEST.fullmatch(self.generator_digest):
            raise ValueError("generator model digest is invalid")
        parse_timestamp(self.generated_at)


@dataclass(frozen=True)
class DimensionFinding:
    dimension: str
    status: Literal["pass", "fail", "unknown"]
    reason_code: str

    def __post_init__(self) -> None:
        if self.dimension not in QA_DIMENSIONS:
            raise ValueError("QA finding dimension is invalid")
        if self.status not in {"pass", "fail", "unknown"}:
            raise ValueError("QA finding status is invalid")
        _identifier("QA reason_code", self.reason_code)


def _validate_findings(findings: tuple[DimensionFinding, ...]) -> None:
    dimensions = tuple(item.dimension for item in findings)
    if len(dimensions) != len(set(dimensions)) or set(dimensions) != set(QA_DIMENSIONS):
        raise ValueError("receipt must contain every QA dimension exactly once")


@dataclass(frozen=True)
class ValidationReceipt:
    artifact_id: str
    artifact_sha256: str
    validator_id: str
    observed_at: str
    findings: tuple[DimensionFinding, ...]

    def __post_init__(self) -> None:
        _identifier("artifact_id", self.artifact_id)
        _sha256("validation", self.artifact_sha256)
        _identifier("validator_id", self.validator_id)
        parse_timestamp(self.observed_at)
        _validate_findings(self.findings)


@dataclass(frozen=True)
class ReviewReceipt:
    artifact_id: str
    artifact_sha256: str
    reviewer_tier: TierName
    reviewer_model: str
    reviewer_digest: str
    reviewer_provider: str
    reviewer_context_id: str
    observed_at: str
    findings: tuple[DimensionFinding, ...]

    def __post_init__(self) -> None:
        _identifier("artifact_id", self.artifact_id)
        _sha256("review", self.artifact_sha256)
        if self.reviewer_tier not in TIER_NAMES or self.reviewer_tier == "no_llm":
            raise ValueError("reviewer must use a model tier")
        for name in ("reviewer_model", "reviewer_provider", "reviewer_context_id"):
            _identifier(name, getattr(self, name))
        if not _MODEL_DIGEST.fullmatch(self.reviewer_digest):
            raise ValueError("reviewer model digest is invalid")
        parse_timestamp(self.observed_at)
        _validate_findings(self.findings)


@dataclass(frozen=True)
class PublishEligibility:
    artifact_id: str
    artifact_sha256: str
    eligible: bool
    reasons: tuple[str, ...]
    reviewer_model: str | None
    reviewer_context_id: str | None
    compliance_registry_sha256: str
    approval_id: str | None


def evaluate_publish_eligibility(
    *,
    deliverable: Deliverable,
    validation: ValidationReceipt,
    review: ReviewReceipt | None,
    compliance: ComplianceDecision,
    approval: ApprovalDecision,
) -> PublishEligibility:
    if validation.artifact_id != deliverable.artifact_id:
        raise ValueError("validation artifact ID does not match deliverable")
    if validation.artifact_sha256 != deliverable.artifact_sha256:
        raise ValueError("validation artifact hash does not match deliverable")
    if review is not None:
        if review.artifact_id != deliverable.artifact_id:
            raise ValueError("review artifact ID does not match deliverable")
        if review.artifact_sha256 != deliverable.artifact_sha256:
            raise ValueError("review artifact hash does not match deliverable")
    if not _SHA256.fullmatch(compliance.registry_sha256):
        raise ValueError("compliance registry hash is invalid")
    if not _SHA256.fullmatch(approval.request_sha256):
        raise ValueError("approval request hash is invalid")

    reasons: list[str] = []
    if any(item.status != "pass" for item in validation.findings):
        reasons.append("deterministic_validation_failed")
    if review is None:
        reasons.append("independent_review_missing")
    else:
        if any(item.status != "pass" for item in review.findings):
            reasons.append("model_review_failed")
        if review.reviewer_context_id == deliverable.generator_context_id:
            reasons.append("review_context_not_independent")
        if deliverable.value_class == "high" and review.reviewer_tier != "escalation":
            reasons.append("high_value_escalation_review_missing")
    if compliance.status is not DecisionStatus.ALLOW:
        reasons.append("compliance_not_allowed")
    if compliance.action != "publish_ai_content":
        reasons.append("compliance_action_mismatch")
    if compliance.platform != deliverable.target_platform:
        reasons.append("compliance_platform_mismatch")
    if approval.status is not ApprovalStatus.ALLOW or approval.approval_id is None:
        reasons.append("human_approval_missing")
    if approval.action != "publish_first_product_in_category":
        reasons.append("approval_action_mismatch")
    if approval.target != deliverable.artifact_id:
        reasons.append("approval_target_mismatch")

    return PublishEligibility(
        artifact_id=deliverable.artifact_id,
        artifact_sha256=deliverable.artifact_sha256,
        eligible=not reasons,
        reasons=tuple(reasons),
        reviewer_model=None if review is None else review.reviewer_model,
        reviewer_context_id=None if review is None else review.reviewer_context_id,
        compliance_registry_sha256=compliance.registry_sha256,
        approval_id=approval.approval_id,
    )
