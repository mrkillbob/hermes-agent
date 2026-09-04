"""Machine-readable compliance policy with conservative action decisions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path


class RegistryError(ValueError):
    """Raised when the compliance registry itself is not trustworthy."""


class DecisionStatus(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    BLOCK_AND_REVIEW = "BLOCK_AND_REVIEW"


REQUIRED_GLOBAL_PROHIBITIONS = frozenset(
    {
        "account_farming",
        "captcha_bypass",
        "counterfeit_or_ip_infringing_goods",
        "deceptive_identities",
        "evading_platform_restrictions",
        "fake_reviews",
        "fake_social_engagement",
        "mass_unsolicited_spam",
        "prohibited_marketplace_bots",
        "survey_answer_automation",
    }
)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")
_ACTION_POLICY_FIELD = {
    "publish_ai_content": "ai_content_policy",
    "scrape": "scraping_policy",
    "use_api": "api_requirements",
    "outreach": "outreach_policy",
}
_RATE_LIMITED_ACTIONS = frozenset({"scrape", "use_api"})
_POLICY_STATUSES = frozenset(status.value for status in DecisionStatus)


@dataclass(frozen=True)
class Rule:
    status: DecisionStatus
    requirements: tuple[str, ...]


@dataclass(frozen=True)
class PlatformPolicy:
    platform: str
    automation_allowed: bool | None
    ai_content_policy: Rule
    scraping_policy: Rule
    api_requirements: Rule
    outreach_policy: Rule
    rate_limits: Mapping[str, object]
    prohibited_behaviors: frozenset[str]
    source: str
    last_verified: str


@dataclass(frozen=True)
class ComplianceDecision:
    status: DecisionStatus
    reason: str
    platform: str
    action: str
    registry_sha256: str
    policy_source: str | None = None
    last_verified: str | None = None
    requirements: tuple[str, ...] = ()
    rate_limits: Mapping[str, int] | None = None


class ComplianceRegistry:
    """Immutable registry that never treats missing policy as permission."""

    def __init__(
        self,
        *,
        policies: Mapping[str, PlatformPolicy],
        global_prohibitions: frozenset[str],
        registry_sha256: str,
    ) -> None:
        self._policies = dict(policies)
        self.global_prohibitions = global_prohibitions
        self.registry_sha256 = registry_sha256

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> ComplianceRegistry:
        if payload.get("schema_version") != "hrl.compliance_registry.v1":
            raise RegistryError("unsupported_schema_version")
        raw_global = payload.get("global_prohibited_behaviors")
        if not isinstance(raw_global, list) or not all(
            isinstance(item, str) for item in raw_global
        ):
            raise RegistryError("invalid_global_prohibitions")
        global_prohibitions = frozenset(
            _normalized_slug(item, "prohibited_behavior") for item in raw_global
        )
        missing = REQUIRED_GLOBAL_PROHIBITIONS - global_prohibitions
        if missing:
            raise RegistryError(
                f"missing_global_prohibitions:{','.join(sorted(missing))}"
            )

        raw_platforms = payload.get("platforms")
        if not isinstance(raw_platforms, list):
            raise RegistryError("platforms_must_be_a_list")
        policies: dict[str, PlatformPolicy] = {}
        for raw_policy in raw_platforms:
            if not isinstance(raw_policy, Mapping):
                raise RegistryError("platform_policy_must_be_an_object")
            policy = _parse_platform_policy(raw_policy)
            if policy.platform in policies:
                raise RegistryError(f"duplicate_platform:{policy.platform}")
            policies[policy.platform] = policy

        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(
            policies=policies,
            global_prohibitions=global_prohibitions,
            registry_sha256=digest,
        )

    def evaluate(self, *, platform: str, action: str) -> ComplianceDecision:
        normalized_platform = _normalized_slug(platform, "platform")
        normalized_action = _normalized_slug(action, "action")
        if normalized_action in self.global_prohibitions:
            return self._decision(
                DecisionStatus.BLOCK,
                "globally_prohibited_behavior",
                normalized_platform,
                normalized_action,
            )

        policy = self._policies.get(normalized_platform)
        if policy is None:
            return self._decision(
                DecisionStatus.BLOCK_AND_REVIEW,
                "platform_not_registered",
                normalized_platform,
                normalized_action,
            )
        if normalized_action in policy.prohibited_behaviors:
            return self._policy_decision(
                DecisionStatus.BLOCK,
                "platform_prohibited_behavior",
                policy,
                normalized_action,
            )
        if policy.automation_allowed is False:
            return self._policy_decision(
                DecisionStatus.BLOCK,
                "automation_blocked",
                policy,
                normalized_action,
            )
        if policy.automation_allowed is not True:
            return self._policy_decision(
                DecisionStatus.BLOCK_AND_REVIEW,
                "automation_policy_unclear",
                policy,
                normalized_action,
            )
        if not policy.source or not _valid_iso_date(policy.last_verified):
            return self._policy_decision(
                DecisionStatus.BLOCK_AND_REVIEW,
                "policy_evidence_incomplete",
                policy,
                normalized_action,
            )
        if normalized_action == "automate":
            return self._policy_decision(
                DecisionStatus.ALLOW,
                "explicit_policy_allow",
                policy,
                normalized_action,
            )

        field = _ACTION_POLICY_FIELD.get(normalized_action)
        if field is None:
            return self._policy_decision(
                DecisionStatus.BLOCK_AND_REVIEW,
                "action_not_registered",
                policy,
                normalized_action,
            )
        rule = getattr(policy, field)
        if rule.status is DecisionStatus.BLOCK:
            return self._policy_decision(
                DecisionStatus.BLOCK,
                f"{field}_blocked",
                policy,
                normalized_action,
                requirements=rule.requirements,
            )
        if rule.status is not DecisionStatus.ALLOW:
            return self._policy_decision(
                DecisionStatus.BLOCK_AND_REVIEW,
                f"{field}_unclear",
                policy,
                normalized_action,
                requirements=rule.requirements,
            )
        rate_limits = None
        if normalized_action in _RATE_LIMITED_ACTIONS:
            rate_limits = _declared_rate_limits(policy.rate_limits)
            if rate_limits is None:
                return self._policy_decision(
                    DecisionStatus.BLOCK_AND_REVIEW,
                    "rate_limits_unclear",
                    policy,
                    normalized_action,
                    requirements=rule.requirements,
                )
        return self._policy_decision(
            DecisionStatus.ALLOW,
            "explicit_policy_allow",
            policy,
            normalized_action,
            requirements=rule.requirements,
            rate_limits=rate_limits,
        )

    def _decision(
        self,
        status: DecisionStatus,
        reason: str,
        platform: str,
        action: str,
    ) -> ComplianceDecision:
        return ComplianceDecision(
            status=status,
            reason=reason,
            platform=platform,
            action=action,
            registry_sha256=self.registry_sha256,
        )

    def _policy_decision(
        self,
        status: DecisionStatus,
        reason: str,
        policy: PlatformPolicy,
        action: str,
        *,
        requirements: tuple[str, ...] = (),
        rate_limits: Mapping[str, int] | None = None,
    ) -> ComplianceDecision:
        return ComplianceDecision(
            status=status,
            reason=reason,
            platform=policy.platform,
            action=action,
            registry_sha256=self.registry_sha256,
            policy_source=policy.source or None,
            last_verified=policy.last_verified or None,
            requirements=requirements,
            rate_limits=rate_limits,
        )


def load_registry(
    path: Path | str = Path("config/compliance_registry.json"),
) -> ComplianceRegistry:
    registry_path = Path(path)
    try:
        parsed = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"registry_unreadable:{exc.__class__.__name__}") from exc
    if not isinstance(parsed, Mapping):
        raise RegistryError("registry_root_must_be_an_object")
    return ComplianceRegistry.from_mapping(parsed)


def _parse_platform_policy(raw: Mapping[str, object]) -> PlatformPolicy:
    automation = raw.get("automation_allowed")
    if automation is not None and not isinstance(automation, bool):
        raise RegistryError("automation_allowed_must_be_boolean_or_null")
    prohibited = raw.get("prohibited_behaviors", [])
    if not isinstance(prohibited, list) or not all(
        isinstance(item, str) for item in prohibited
    ):
        raise RegistryError("invalid_platform_prohibitions")
    return PlatformPolicy(
        platform=_normalized_slug(raw.get("platform"), "platform"),
        automation_allowed=automation,
        ai_content_policy=_parse_rule(
            raw.get("ai_content_policy"), "ai_content_policy"
        ),
        scraping_policy=_parse_rule(raw.get("scraping_policy"), "scraping_policy"),
        api_requirements=_parse_rule(raw.get("api_requirements"), "api_requirements"),
        outreach_policy=_parse_rule(raw.get("outreach_policy"), "outreach_policy"),
        rate_limits=raw.get("rate_limits")
        if isinstance(raw.get("rate_limits"), Mapping)
        else {},
        prohibited_behaviors=frozenset(
            _normalized_slug(item, "prohibited_behavior") for item in prohibited
        ),
        source=str(raw.get("source") or "").strip(),
        last_verified=str(raw.get("last_verified") or "").strip(),
    )


def _parse_rule(value: object, field: str) -> Rule:
    if not isinstance(value, Mapping):
        return Rule(DecisionStatus.BLOCK_AND_REVIEW, ())
    raw_status = str(value.get("status") or "BLOCK_AND_REVIEW").strip().upper()
    if raw_status not in _POLICY_STATUSES:
        raise RegistryError(f"invalid_policy_status:{field}")
    raw_requirements = value.get("requirements", [])
    if not isinstance(raw_requirements, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_requirements
    ):
        raise RegistryError(f"invalid_policy_requirements:{field}")
    return Rule(
        DecisionStatus(raw_status), tuple(item.strip() for item in raw_requirements)
    )


def _declared_rate_limits(value: Mapping[str, object]) -> dict[str, int] | None:
    if str(value.get("status") or "").strip().upper() != "DECLARED":
        return None
    maximum = value.get("max_requests")
    period = value.get("period_seconds")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
        return None
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        return None
    return {"max_requests": maximum, "period_seconds": period}


def _normalized_slug(value: object, field: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SLUG.fullmatch(text):
        raise RegistryError(f"invalid_{field}")
    return text


def _valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
