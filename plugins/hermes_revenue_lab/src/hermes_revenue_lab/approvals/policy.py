"""Deterministic approval policy; model output can never grant permission."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path


class PolicyError(ValueError):
    """Raised when approval policy or request data is malformed."""


class ApprovalStatus(str, Enum):
    ALLOW = "ALLOW"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


REQUIRED_HUMAN_ACTIONS = frozenset(
    {
        "accept_new_terms",
        "change_price_materially",
        "create_marketplace_account",
        "enter_contract",
        "handle_sensitive_customer_data",
        "issue_refund",
        "publish_first_product_in_category",
        "send_novel_outbound_campaign",
        "spend_money",
        "start_paid_advertising",
        "subscribe_paid_api",
    }
)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    target: str
    parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _slug(self.action, "action"))
        target = str(self.target or "").strip()
        if not target or len(target) > 512:
            raise PolicyError("invalid_target")
        object.__setattr__(self, "target", target)
        if not isinstance(self.parameters, Mapping):
            raise PolicyError("parameters_must_be_an_object")
        try:
            json.dumps(
                self.parameters, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise PolicyError("parameters_not_canonical_json") from exc

    @property
    def sha256(self) -> str:
        canonical = json.dumps(
            {
                "action": self.action,
                "parameters": self.parameters,
                "target": self.target,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    request_sha256: str
    approver: str
    approved_at: datetime
    expires_at: datetime
    signature: str


@dataclass(frozen=True)
class ApprovalDecision:
    status: ApprovalStatus
    reason: str
    request_sha256: str
    action: str
    target: str
    approval_id: str | None = None


@dataclass(frozen=True)
class ApprovalPolicy:
    authorized_approvers: frozenset[str]
    local_safe_actions: frozenset[str]
    required_human_actions: frozenset[str]
    policy_sha256: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> ApprovalPolicy:
        if payload.get("schema_version") != "hrl.approval_policy.v1":
            raise PolicyError("unsupported_schema_version")
        approvers = _slug_set(
            payload.get("authorized_approvers"), "authorized_approvers"
        )
        if not approvers:
            raise PolicyError("authorized_approvers_empty")
        safe = _slug_set(payload.get("local_safe_actions"), "local_safe_actions")
        required = _slug_set(
            payload.get("initial_approval_required"),
            "initial_approval_required",
        )
        missing = REQUIRED_HUMAN_ACTIONS - required
        if missing:
            raise PolicyError(
                f"missing_required_human_actions:{','.join(sorted(missing))}"
            )
        overlap = safe & required
        if overlap:
            raise PolicyError(f"action_policy_overlap:{','.join(sorted(overlap))}")
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return cls(
            authorized_approvers=approvers,
            local_safe_actions=safe,
            required_human_actions=required,
            policy_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )


class ApprovalGate:
    def __init__(
        self,
        policy: ApprovalPolicy,
        *,
        verification_key: bytes | None = None,
    ) -> None:
        self.policy = policy
        if verification_key is not None and len(verification_key) < 16:
            raise PolicyError("verification_key_too_short")
        self._verification_key = verification_key

    def evaluate(
        self,
        request: ApprovalRequest,
        *,
        approvals: Iterable[ApprovalRecord] = (),
        now: datetime | None = None,
    ) -> ApprovalDecision:
        if request.action in self.policy.local_safe_actions:
            return self._decision(ApprovalStatus.ALLOW, "local_safe_action", request)
        if request.action not in self.policy.required_human_actions:
            return self._decision(
                ApprovalStatus.APPROVAL_REQUIRED,
                "action_not_classified",
                request,
            )
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None:
            raise PolicyError("now_must_be_timezone_aware")
        for approval in approvals:
            if self._matches(approval, request=request, now=observed_at):
                return self._decision(
                    ApprovalStatus.ALLOW,
                    "exact_human_approval",
                    request,
                    approval_id=approval.approval_id,
                )
        return self._decision(
            ApprovalStatus.APPROVAL_REQUIRED,
            "human_approval_missing",
            request,
        )

    def _matches(
        self,
        approval: ApprovalRecord,
        *,
        request: ApprovalRequest,
        now: datetime,
    ) -> bool:
        if self._verification_key is None:
            return False
        if approval.approver not in self.policy.authorized_approvers:
            return False
        if approval.request_sha256 != request.sha256:
            return False
        if not str(approval.approval_id or "").strip():
            return False
        if approval.approved_at.tzinfo is None or approval.expires_at.tzinfo is None:
            return False
        if not approval.approved_at <= now < approval.expires_at:
            return False
        expected = hmac.new(
            self._verification_key,
            _approval_payload(approval).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, str(approval.signature or ""))

    @staticmethod
    def _decision(
        status: ApprovalStatus,
        reason: str,
        request: ApprovalRequest,
        *,
        approval_id: str | None = None,
    ) -> ApprovalDecision:
        return ApprovalDecision(
            status=status,
            reason=reason,
            request_sha256=request.sha256,
            action=request.action,
            target=request.target,
            approval_id=approval_id,
        )


def load_approval_policy(
    path: Path | str = Path("config/approval_policy.json"),
) -> ApprovalPolicy:
    policy_path = Path(path)
    try:
        parsed = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(
            f"approval_policy_unreadable:{exc.__class__.__name__}"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise PolicyError("approval_policy_root_must_be_an_object")
    return ApprovalPolicy.from_mapping(parsed)


def _slug(value: object, field: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SLUG.fullmatch(text):
        raise PolicyError(f"invalid_{field}")
    return text


def _slug_set(value: object, field: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PolicyError(f"{field}_must_be_a_string_list")
    normalized = tuple(_slug(item, field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise PolicyError(f"duplicate_{field}")
    return frozenset(normalized)


def _approval_payload(approval: ApprovalRecord) -> str:
    return json.dumps(
        {
            "approval_id": approval.approval_id,
            "approved_at": approval.approved_at.isoformat(),
            "approver": approval.approver,
            "expires_at": approval.expires_at.isoformat(),
            "request_sha256": approval.request_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
