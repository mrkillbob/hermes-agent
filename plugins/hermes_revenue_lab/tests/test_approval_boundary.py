from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hermes_revenue_lab.approvals import (
    REQUIRED_HUMAN_ACTIONS,
    ApprovalGate,
    ApprovalPolicy,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    PolicyError,
    load_approval_policy,
)


def _policy_payload() -> dict[str, object]:
    return {
        "schema_version": "hrl.approval_policy.v1",
        "authorized_approvers": ["owner"],
        "local_safe_actions": [
            "calculate_metrics",
            "generate_private_draft",
            "read_public_policy",
            "run_dry_run",
        ],
        "initial_approval_required": sorted(REQUIRED_HUMAN_ACTIONS),
    }


def _signed_approval(
    *,
    key: bytes,
    approval_id: str,
    request: ApprovalRequest,
    approver: str,
    approved_at: datetime,
    expires_at: datetime,
) -> ApprovalRecord:
    payload = {
        "approval_id": approval_id,
        "approved_at": approved_at.isoformat(),
        "approver": approver,
        "expires_at": expires_at.isoformat(),
        "request_sha256": request.sha256,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return ApprovalRecord(
        approval_id=approval_id,
        request_sha256=request.sha256,
        approver=approver,
        approved_at=approved_at,
        expires_at=expires_at,
        signature=signature,
    )


@pytest.mark.parametrize("action", sorted(REQUIRED_HUMAN_ACTIONS))
def test_consequential_actions_require_human_approval(action: str) -> None:
    gate = ApprovalGate(ApprovalPolicy.from_mapping(_policy_payload()))
    request = ApprovalRequest(action=action, target="example", parameters={})

    decision = gate.evaluate(request)

    assert decision.status is ApprovalStatus.APPROVAL_REQUIRED
    assert decision.reason == "human_approval_missing"
    assert decision.request_sha256 == request.sha256


def test_local_read_only_and_private_draft_actions_are_allowed() -> None:
    gate = ApprovalGate(ApprovalPolicy.from_mapping(_policy_payload()))

    for action in (
        "calculate_metrics",
        "generate_private_draft",
        "read_public_policy",
        "run_dry_run",
    ):
        decision = gate.evaluate(
            ApprovalRequest(action=action, target="local", parameters={})
        )
        assert decision.status is ApprovalStatus.ALLOW
        assert decision.reason == "local_safe_action"


def test_unknown_mutation_fails_closed() -> None:
    gate = ApprovalGate(ApprovalPolicy.from_mapping(_policy_payload()))

    decision = gate.evaluate(
        ApprovalRequest(
            action="new_external_mutation", target="somewhere", parameters={}
        )
    )

    assert decision.status is ApprovalStatus.APPROVAL_REQUIRED
    assert decision.reason == "action_not_classified"


def test_approval_is_bound_to_exact_scope_and_expiry() -> None:
    policy = ApprovalPolicy.from_mapping(_policy_payload())
    verification_key = b"test-only-operator-key"
    gate = ApprovalGate(policy, verification_key=verification_key)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    request = ApprovalRequest(
        action="spend_money",
        target="example_ad_campaign",
        parameters={"currency": "USD", "maximum_amount": "25.00"},
    )
    approval = _signed_approval(
        key=verification_key,
        approval_id="approval-001",
        request=request,
        approver="owner",
        approved_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )

    allowed = gate.evaluate(request, approvals=[approval], now=now)
    assert allowed.status is ApprovalStatus.ALLOW
    assert allowed.reason == "exact_human_approval"
    assert allowed.approval_id == "approval-001"

    changed_amount = ApprovalRequest(
        action="spend_money",
        target="example_ad_campaign",
        parameters={"currency": "USD", "maximum_amount": "26.00"},
    )
    assert gate.evaluate(changed_amount, approvals=[approval], now=now).status is (
        ApprovalStatus.APPROVAL_REQUIRED
    )
    assert (
        gate.evaluate(
            request,
            approvals=[
                _signed_approval(
                    key=verification_key,
                    approval_id="approval-expired",
                    request=request,
                    approver="owner",
                    approved_at=now - timedelta(hours=2),
                    expires_at=now - timedelta(seconds=1),
                )
            ],
            now=now,
        ).status
        is ApprovalStatus.APPROVAL_REQUIRED
    )


def test_model_or_agent_cannot_approve_a_request() -> None:
    policy = ApprovalPolicy.from_mapping(_policy_payload())
    verification_key = b"test-only-operator-key"
    gate = ApprovalGate(policy, verification_key=verification_key)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    request = ApprovalRequest(action="enter_contract", target="vendor", parameters={})
    model_approval = _signed_approval(
        key=verification_key,
        approval_id="model-approval",
        request=request,
        approver="model:qwen3.5:4b",
        approved_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )

    decision = gate.evaluate(request, approvals=[model_approval], now=now)

    assert decision.status is ApprovalStatus.APPROVAL_REQUIRED
    assert decision.reason == "human_approval_missing"


def test_unsigned_owner_claim_cannot_approve_a_request() -> None:
    policy = ApprovalPolicy.from_mapping(_policy_payload())
    gate = ApprovalGate(policy, verification_key=b"test-only-operator-key")
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    request = ApprovalRequest(action="issue_refund", target="order-123", parameters={})
    fabricated = ApprovalRecord(
        approval_id="fabricated",
        request_sha256=request.sha256,
        approver="owner",
        approved_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        signature="",
    )

    decision = gate.evaluate(request, approvals=[fabricated], now=now)

    assert decision.status is ApprovalStatus.APPROVAL_REQUIRED
    assert decision.reason == "human_approval_missing"


def test_policy_rejects_missing_required_actions_and_overlapping_safe_actions() -> None:
    incomplete = _policy_payload()
    incomplete["initial_approval_required"] = ["spend_money"]
    with pytest.raises(PolicyError, match="missing_required_human_actions"):
        ApprovalPolicy.from_mapping(incomplete)

    overlap = _policy_payload()
    overlap["local_safe_actions"] = ["spend_money"]
    with pytest.raises(PolicyError, match="action_policy_overlap"):
        ApprovalPolicy.from_mapping(overlap)


def test_repository_policy_contains_no_default_approval_grants() -> None:
    path = Path(__file__).parents[1] / "config" / "approval_policy.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    policy = load_approval_policy(path)

    assert "approvals" not in raw
    assert policy.required_human_actions == REQUIRED_HUMAN_ACTIONS
    assert policy.authorized_approvers == frozenset({"owner"})
