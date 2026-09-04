"""Fail-closed experiment-capital recommendations with no spending authority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _decimal(name: str, value: object, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(
            f"{name} must be a finite Decimal" + (" or unknown" if optional else "")
        )


def _ratio(name: str, value: object, *, optional: bool = False) -> None:
    _decimal(name, value, optional=optional)
    if value is not None and not Decimal(0) <= value <= Decimal(1):
        raise ValueError(f"{name} ratio must be between zero and one")


def _refs(name: str, values: tuple[str, ...], *, required: bool) -> None:
    if required and not values:
        raise ValueError(f"{name} are required")
    if len(values) != len(set(values)) or any(
        not isinstance(value, str) or not 1 <= len(value) <= 1_000 for value in values
    ):
        raise ValueError(f"{name} must be unique bounded strings")


@dataclass(frozen=True)
class CapitalEvidence:
    experiment_id: str
    contribution_margin: Decimal | None
    real_customer_count: int | None
    automation_success_ratio: Decimal | None
    minimum_stable_windows: int
    observed_stable_windows: int | None
    compliance_green: bool | None
    human_minutes_per_fulfillment: Decimal | None
    acceptable_human_minutes: Decimal
    customer_evidence_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    minimum_automation_ratio: Decimal = Decimal("0.80")

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not _IDENTIFIER.fullmatch(
            self.experiment_id
        ):
            raise ValueError("experiment_id is invalid")
        _decimal("contribution margin", self.contribution_margin, optional=True)
        if self.real_customer_count is not None and (
            type(self.real_customer_count) is not int or self.real_customer_count < 0
        ):
            raise ValueError(
                "real customer count must be a nonnegative integer or unknown"
            )
        _ratio("automation", self.automation_success_ratio, optional=True)
        _ratio("minimum automation", self.minimum_automation_ratio)
        if (
            type(self.minimum_stable_windows) is not int
            or self.minimum_stable_windows < 1
        ):
            raise ValueError("minimum stable windows must be a positive integer")
        if self.observed_stable_windows is not None and (
            type(self.observed_stable_windows) is not int
            or self.observed_stable_windows < 0
        ):
            raise ValueError("observed stable windows must be nonnegative or unknown")
        if (
            self.compliance_green is not None
            and type(self.compliance_green) is not bool
        ):
            raise ValueError("compliance green must be a boolean or unknown")
        _decimal(
            "human minutes per fulfillment",
            self.human_minutes_per_fulfillment,
            optional=True,
        )
        _decimal("acceptable human minutes", self.acceptable_human_minutes)
        if (
            self.human_minutes_per_fulfillment is not None
            and self.human_minutes_per_fulfillment < 0
        ):
            raise ValueError("human minutes per fulfillment must be nonnegative")
        if self.acceptable_human_minutes <= 0:
            raise ValueError("acceptable human minutes must be positive")
        _refs("source references", self.source_refs, required=True)
        _refs(
            "customer evidence references",
            self.customer_evidence_refs,
            required=bool(self.real_customer_count),
        )


@dataclass(frozen=True)
class CapitalRecommendation:
    experiment_id: str
    action: Literal["recommend_increase", "hold", "modify", "kill"]
    blocking_reasons: tuple[str, ...]
    requires_human_approval: bool = True
    actual_spend_allowed: bool = False
    authority: str = "recommendation_only"


def recommend_capital_action(evidence: CapitalEvidence) -> CapitalRecommendation:
    """Return a deterministic recommendation; never grant spending authority."""

    reasons: list[str] = []
    if evidence.contribution_margin is None:
        reasons.append("contribution_margin_unknown")
    elif evidence.contribution_margin < 0:
        reasons.append("negative_contribution_margin")
    elif evidence.contribution_margin == 0:
        reasons.append("contribution_margin_not_positive")

    if evidence.real_customer_count is None:
        reasons.append("real_customer_evidence_unknown")
    elif evidence.real_customer_count == 0 or not evidence.customer_evidence_refs:
        reasons.append("real_customer_evidence_missing")

    if evidence.automation_success_ratio is None:
        reasons.append("automation_unknown")
    elif evidence.automation_success_ratio < evidence.minimum_automation_ratio:
        reasons.append("automation_below_threshold")

    if evidence.observed_stable_windows is None:
        reasons.append("stability_unknown")
    elif evidence.observed_stable_windows < evidence.minimum_stable_windows:
        reasons.append("automation_not_stable")

    if evidence.compliance_green is None:
        reasons.append("compliance_unknown")
    elif not evidence.compliance_green:
        reasons.append("compliance_not_green")

    if evidence.human_minutes_per_fulfillment is None:
        reasons.append("human_burden_unknown")
    elif evidence.human_minutes_per_fulfillment > evidence.acceptable_human_minutes:
        reasons.append("human_burden_excessive")

    reason_set = set(reasons)
    if {"negative_contribution_margin", "compliance_not_green"} & reason_set:
        action: Literal["recommend_increase", "hold", "modify", "kill"] = "kill"
    elif not reasons:
        action = "recommend_increase"
    elif (
        any(reason.endswith("unknown") for reason in reasons)
        or {
            "real_customer_evidence_missing",
            "contribution_margin_not_positive",
        }
        & reason_set
    ):
        action = "hold"
    else:
        action = "modify"

    return CapitalRecommendation(
        experiment_id=evidence.experiment_id,
        action=action,
        blocking_reasons=tuple(reasons),
    )
