from __future__ import annotations

import unittest
from dataclasses import replace

from hermes_revenue_lab.approvals import ApprovalDecision, ApprovalStatus
from hermes_revenue_lab.compliance import ComplianceDecision, DecisionStatus
from hermes_revenue_lab.qa import (
    QA_DIMENSIONS,
    Deliverable,
    DimensionFinding,
    ReviewReceipt,
    ValidationReceipt,
    evaluate_publish_eligibility,
)

SHA = "a" * 64
OBSERVED = "2026-08-21T23:00:00+00:00"


def deliverable(**overrides: object) -> Deliverable:
    values = {
        "artifact_id": "calculator-001",
        "artifact_sha256": SHA,
        "artifact_type": "calculator",
        "value_class": "low",
        "target_platform": "example_marketplace",
        "generator_model": "qwen3.5:4b",
        "generator_digest": "2a654d98e6fb",
        "generator_provider": "ollama-launch",
        "generator_context_id": "generator-context-001",
        "generated_at": OBSERVED,
    }
    values.update(overrides)
    return Deliverable(**values)


def findings(status: str = "pass") -> tuple[DimensionFinding, ...]:
    return tuple(
        DimensionFinding(dimension, status, f"{dimension}_{status}")
        for dimension in QA_DIMENSIONS
    )


def validation(**overrides: object) -> ValidationReceipt:
    values = {
        "artifact_id": "calculator-001",
        "artifact_sha256": SHA,
        "validator_id": "deterministic-validator-v1",
        "observed_at": OBSERVED,
        "findings": findings(),
    }
    values.update(overrides)
    return ValidationReceipt(**values)


def review(**overrides: object) -> ReviewReceipt:
    values = {
        "artifact_id": "calculator-001",
        "artifact_sha256": SHA,
        "reviewer_tier": "fast",
        "reviewer_model": "qwen3.5:4b",
        "reviewer_digest": "2a654d98e6fb",
        "reviewer_provider": "ollama-launch",
        "reviewer_context_id": "review-context-002",
        "observed_at": OBSERVED,
        "findings": findings(),
    }
    values.update(overrides)
    return ReviewReceipt(**values)


def compliance(status: DecisionStatus = DecisionStatus.ALLOW) -> ComplianceDecision:
    return ComplianceDecision(
        status=status,
        reason="explicit_policy_allow"
        if status is DecisionStatus.ALLOW
        else "policy_unclear",
        platform="example_marketplace",
        action="publish_ai_content",
        registry_sha256="b" * 64,
        policy_source="https://example.test/policy",
        last_verified="2026-08-21",
    )


def approval(status: ApprovalStatus = ApprovalStatus.ALLOW) -> ApprovalDecision:
    return ApprovalDecision(
        status=status,
        reason="exact_human_approval"
        if status is ApprovalStatus.ALLOW
        else "human_approval_missing",
        request_sha256="c" * 64,
        action="publish_first_product_in_category",
        target="calculator-001",
        approval_id="approval-001" if status is ApprovalStatus.ALLOW else None,
    )


class DeliverableQATest(unittest.TestCase):
    def test_complete_low_value_pipeline_can_become_publish_eligible(self) -> None:
        verdict = evaluate_publish_eligibility(
            deliverable=deliverable(),
            validation=validation(),
            review=review(),
            compliance=compliance(),
            approval=approval(),
        )
        self.assertTrue(verdict.eligible)
        self.assertEqual((), verdict.reasons)
        self.assertEqual(SHA, verdict.artifact_sha256)

    def test_missing_or_failed_stage_blocks_publication(self) -> None:
        no_review = evaluate_publish_eligibility(
            deliverable=deliverable(),
            validation=validation(),
            review=None,
            compliance=compliance(),
            approval=approval(),
        )
        self.assertFalse(no_review.eligible)
        self.assertIn("independent_review_missing", no_review.reasons)

        failed = evaluate_publish_eligibility(
            deliverable=deliverable(),
            validation=replace(validation(), findings=findings("fail")),
            review=review(),
            compliance=compliance(),
            approval=approval(),
        )
        self.assertIn("deterministic_validation_failed", failed.reasons)

    def test_reviewer_must_use_a_distinct_context(self) -> None:
        verdict = evaluate_publish_eligibility(
            deliverable=deliverable(),
            validation=validation(),
            review=replace(review(), reviewer_context_id="generator-context-001"),
            compliance=compliance(),
            approval=approval(),
        )
        self.assertIn("review_context_not_independent", verdict.reasons)

    def test_high_value_work_requires_unscheduled_escalation_tier_review(self) -> None:
        blocked = evaluate_publish_eligibility(
            deliverable=deliverable(value_class="high"),
            validation=validation(),
            review=review(),
            compliance=compliance(),
            approval=approval(),
        )
        self.assertIn("high_value_escalation_review_missing", blocked.reasons)
        eligible = evaluate_publish_eligibility(
            deliverable=deliverable(value_class="high"),
            validation=validation(),
            review=replace(
                review(),
                reviewer_tier="escalation",
                reviewer_model="review-model:27b",
                reviewer_digest="d" * 12,
            ),
            compliance=compliance(),
            approval=approval(),
        )
        self.assertTrue(eligible.eligible)

    def test_compliance_and_authenticated_approval_remain_load_bearing(self) -> None:
        policy_block = evaluate_publish_eligibility(
            deliverable=deliverable(),
            validation=validation(),
            review=review(),
            compliance=compliance(DecisionStatus.BLOCK_AND_REVIEW),
            approval=approval(),
        )
        self.assertIn("compliance_not_allowed", policy_block.reasons)
        approval_block = evaluate_publish_eligibility(
            deliverable=deliverable(),
            validation=validation(),
            review=review(),
            compliance=compliance(),
            approval=approval(ApprovalStatus.APPROVAL_REQUIRED),
        )
        self.assertIn("human_approval_missing", approval_block.reasons)
        platform_mismatch = evaluate_publish_eligibility(
            deliverable=replace(deliverable(), target_platform="other_marketplace"),
            validation=validation(),
            review=review(),
            compliance=compliance(),
            approval=approval(),
        )
        self.assertIn("compliance_platform_mismatch", platform_mismatch.reasons)

    def test_all_receipts_are_hash_bound_and_dimension_complete(self) -> None:
        with self.assertRaisesRegex(ValueError, "artifact hash"):
            evaluate_publish_eligibility(
                deliverable=deliverable(),
                validation=replace(validation(), artifact_sha256="e" * 64),
                review=review(),
                compliance=compliance(),
                approval=approval(),
            )
        with self.assertRaisesRegex(ValueError, "every QA dimension"):
            replace(validation(), findings=findings()[:-1])


if __name__ == "__main__":
    unittest.main()
