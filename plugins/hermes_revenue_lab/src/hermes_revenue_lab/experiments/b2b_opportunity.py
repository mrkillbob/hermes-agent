"""HRL-8 one-vertical B2B opportunity intelligence contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from hermes_revenue_lab.scouts import ScoutCandidate, evaluate_candidate


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True)
class VerticalSelection:
    vertical_id: str
    business_type: str
    market: str


SELECTED_VERTICAL = VerticalSelection(
    vertical_id="independent_hvac_sacramento_ca",
    business_type="independent HVAC contractors",
    market="Sacramento County, California",
)


@dataclass(frozen=True)
class BusinessTarget:
    target_id: str
    vertical_id: str
    scout_candidate: ScoutCandidate

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.target_id):
            raise ValueError("business target ID is invalid")
        if self.vertical_id != SELECTED_VERTICAL.vertical_id:
            raise ValueError("HRL-8 target is outside the selected vertical")
        if self.scout_candidate.scout_kind != "business_problem":
            raise ValueError("HRL-8 target must come from the Business Problem Scout")


@dataclass(frozen=True)
class AuditFinding:
    finding_id: str
    target_id: str
    problem: str
    evidence_ids: tuple[str, ...]
    consequence: str
    remedy: str
    confidence: str
    competitor_comparison: str | None = None

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.finding_id):
            raise ValueError("audit finding ID is invalid")
        if not self.evidence_ids:
            raise ValueError("audit finding requires evidence")
        if self.confidence not in {"high", "very_high"}:
            raise ValueError("HRL-8 findings must be high confidence")
        for name in ("problem", "consequence", "remedy"):
            value = getattr(self, name)
            if not isinstance(value, str) or not 1 <= len(value) <= 2_000:
                raise ValueError(f"audit {name} is invalid")


@dataclass(frozen=True)
class PriceHypothesis:
    product: str
    amount: Decimal
    cadence: str
    status: str = "hypothesis"

    def __post_init__(self) -> None:
        bounds = {
            "basic_diagnostic": (Decimal("49"), Decimal("49"), "one_time"),
            "detailed_audit": (Decimal("99"), Decimal("99"), "one_time"),
            "competitor_audit": (Decimal("149"), Decimal("299"), "one_time"),
            "monitoring": (Decimal("49"), Decimal("199"), "monthly"),
        }
        if self.product not in bounds:
            raise ValueError("price hypothesis product is invalid")
        minimum, maximum, cadence = bounds[self.product]
        if not minimum <= self.amount <= maximum or self.cadence != cadence:
            raise ValueError("price hypothesis is outside the specified range")
        if self.status != "hypothesis":
            raise ValueError("HRL-8 prices must remain hypotheses")


DEFAULT_PRICE_HYPOTHESES = (
    PriceHypothesis("basic_diagnostic", Decimal("49"), "one_time"),
    PriceHypothesis("detailed_audit", Decimal("99"), "one_time"),
    PriceHypothesis("competitor_audit", Decimal("149"), "one_time"),
    PriceHypothesis("monitoring", Decimal("49"), "monthly"),
)


@dataclass(frozen=True)
class ExperimentABatch:
    vertical: VerticalSelection
    targets: tuple[BusinessTarget, ...]
    findings: tuple[AuditFinding, ...]
    price_hypotheses: tuple[PriceHypothesis, ...]


def build_experiment_a(
    targets: tuple[BusinessTarget, ...],
    findings: tuple[AuditFinding, ...],
    *,
    price_hypotheses: tuple[PriceHypothesis, ...] = DEFAULT_PRICE_HYPOTHESES,
) -> ExperimentABatch:
    if not 80 <= len(targets) <= 120:
        raise ValueError("HRL-8 requires an approximately 100-business cohort")
    target_map = {target.target_id: target for target in targets}
    if len(target_map) != len(targets):
        raise ValueError("HRL-8 target IDs must be unique")
    if not 10 <= len(findings) <= 20:
        raise ValueError("HRL-8 requires 10 to 20 high-confidence problems")
    if len({finding.finding_id for finding in findings}) != len(findings):
        raise ValueError("HRL-8 finding IDs must be unique")
    for finding in findings:
        target = target_map.get(finding.target_id)
        if target is None:
            raise ValueError("audit finding target is outside the cohort")
        verdict = evaluate_candidate(target.scout_candidate)
        if not verdict.eligible:
            raise ValueError("audit finding target lacks objective scout evidence")
        evidence_ids = {item.evidence_id for item in target.scout_candidate.evidence}
        if not set(finding.evidence_ids) <= evidence_ids:
            raise ValueError("audit finding cites evidence outside its target")
    if {item.product for item in price_hypotheses} != {
        "basic_diagnostic",
        "detailed_audit",
        "competitor_audit",
        "monitoring",
    }:
        raise ValueError("HRL-8 requires all four pricing hypotheses")
    return ExperimentABatch(SELECTED_VERTICAL, targets, findings, price_hypotheses)


def render_sample_audit(finding: AuditFinding) -> dict[str, object]:
    return {
        "finding_id": finding.finding_id,
        "target_id": finding.target_id,
        "problem": finding.problem,
        "evidence_ids": list(finding.evidence_ids),
        "consequence": finding.consequence,
        "practical_remedy": finding.remedy,
        "competitor_comparison": finding.competitor_comparison,
        "status": "sample_not_customer_contact",
    }
