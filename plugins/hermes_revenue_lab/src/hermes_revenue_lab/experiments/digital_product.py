"""HRL-10 bounded research and demand gates for functional digital products."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from hermes_revenue_lab.ledger.types import parse_timestamp
from hermes_revenue_lab.scouts import ScoutCandidate, evaluate_candidate

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_FUNCTIONAL_ASSET_TYPES = {
    "calculator",
    "spreadsheet",
    "business_template",
    "planning_tool",
    "niche_reference",
    "specialized_utility",
    "professional_checklist",
    "inventory_tool",
}
_HIGH_CONFIDENCE_CODES = {
    "product_type",
    "demonstrable_demand",
    "buyer_language",
    "existing_paid_alternative",
}


def _identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is invalid")


def _nonnegative_optional_decimal(name: str, value: object) -> None:
    if value is not None and (
        not isinstance(value, Decimal) or not value.is_finite() or value < 0
    ):
        raise ValueError(f"{name} must be a nonnegative finite Decimal or unknown")


def _nonnegative_optional_count(name: str, value: object) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError(f"{name} must be a nonnegative integer or unknown")


@dataclass(frozen=True)
class NicheResearch:
    candidates: tuple[ScoutCandidate, ...]
    observed_at: str

    def __post_init__(self) -> None:
        parse_timestamp(self.observed_at)
        if not 36 <= len(self.candidates) <= 500:
            raise ValueError(
                "digital-product research requires at least 36 bounded candidates"
            )
        identities = tuple(item.candidate_id for item in self.candidates)
        subjects = tuple(item.subject.casefold().strip() for item in self.candidates)
        if len(identities) != len(set(identities)) or len(subjects) != len(
            set(subjects)
        ):
            raise ValueError("digital-product research candidates must be unique")
        for item in self.candidates:
            verdict = evaluate_candidate(item)
            if item.scout_kind != "digital_product" or not verdict.eligible:
                raise ValueError(
                    "research requires an eligible digital-product scout candidate"
                )


@dataclass(frozen=True)
class ProductSpecification:
    product_id: str
    candidate_id: str
    title: str
    asset_type: str
    functional_requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier("product_id", self.product_id)
        _identifier("candidate_id", self.candidate_id)
        if not isinstance(self.title, str) or not 1 <= len(self.title.strip()) <= 200:
            raise ValueError("product title is invalid")
        if self.asset_type not in _FUNCTIONAL_ASSET_TYPES:
            raise ValueError("product must be a permitted functional asset")
        if not 2 <= len(self.functional_requirements) <= 20:
            raise ValueError("product requires two to 20 functional requirements")
        if any(
            not isinstance(item, str) or not _IDENTIFIER.fullmatch(item)
            for item in self.functional_requirements
        ):
            raise ValueError("functional requirement is invalid")
        if len(self.functional_requirements) != len(set(self.functional_requirements)):
            raise ValueError("functional requirements must be unique")


@dataclass(frozen=True)
class InitialPortfolio:
    research_observed_at: str
    products: tuple[ProductSpecification, ...]
    status: str = "private_prototype"
    marketplace: str | None = None


def _high_confidence(candidate: ScoutCandidate) -> bool:
    codes = {item.fact_code for item in candidate.evidence}
    sources = {item.source_url for item in candidate.evidence}
    return _HIGH_CONFIDENCE_CODES <= codes and len(sources) >= 3


def build_initial_portfolio(
    research: NicheResearch,
    specifications: tuple[ProductSpecification, ...],
) -> InitialPortfolio:
    if not 3 <= len(specifications) <= 5:
        raise ValueError("initial portfolio requires three to five products")
    product_ids = tuple(item.product_id for item in specifications)
    candidate_ids = tuple(item.candidate_id for item in specifications)
    if len(product_ids) != len(set(product_ids)) or len(candidate_ids) != len(
        set(candidate_ids)
    ):
        raise ValueError("initial portfolio product and candidate IDs must be unique")
    candidates = {item.candidate_id: item for item in research.candidates}
    for specification in specifications:
        candidate = candidates.get(specification.candidate_id)
        if candidate is None:
            raise ValueError("product specification is outside the candidate research")
        if not _high_confidence(candidate):
            raise ValueError("product selection lacks high-confidence evidence")
        observed_types = {
            item.fact_value
            for item in candidate.evidence
            if item.fact_code == "product_type"
        }
        if specification.asset_type not in observed_types:
            raise ValueError("product asset type is not bound to candidate evidence")
    return InitialPortfolio(research.observed_at, specifications)


@dataclass(frozen=True)
class ListingMetrics:
    product_id: str
    impressions: int | None
    clicks: int | None
    favorites: int | None
    sales: int | None
    price: Decimal | None
    fees: Decimal | None
    refunds: int | None
    observed_at: str

    def __post_init__(self) -> None:
        _identifier("product_id", self.product_id)
        parse_timestamp(self.observed_at)
        for name in ("impressions", "clicks", "favorites", "sales", "refunds"):
            _nonnegative_optional_count(name, getattr(self, name))
        for name in ("price", "fees"):
            _nonnegative_optional_decimal(name, getattr(self, name))
        if (
            self.impressions is not None
            and self.clicks is not None
            and self.clicks > self.impressions
        ) or (
            self.clicks is not None
            and self.sales is not None
            and self.sales > self.clicks
        ):
            raise ValueError("listing metrics violate the observed funnel")
        if (
            self.sales is not None
            and self.refunds is not None
            and self.refunds > self.sales
        ):
            raise ValueError("listing refunds cannot exceed sales")

    @property
    def conversion(self) -> Decimal | None:
        if self.sales is None or self.impressions in {None, 0}:
            return None
        return Decimal(self.sales) / Decimal(self.impressions)

    @property
    def net_revenue(self) -> Decimal | None:
        if None in (self.sales, self.price, self.fees, self.refunds):
            return None
        return self.price * Decimal(self.sales - self.refunds) - self.fees  # type: ignore[operator]


@dataclass(frozen=True)
class SkuExpansionVerdict:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_sku_expansion(
    portfolio: InitialPortfolio,
    metrics: tuple[ListingMetrics, ...],
) -> SkuExpansionVerdict:
    expected = {item.product_id for item in portfolio.products}
    observed = {item.product_id for item in metrics}
    if len(observed) != len(metrics) or observed != expected:
        raise ValueError(
            "SKU expansion requires exact metrics for every initial product"
        )
    reasons: set[str] = set()
    for item in metrics:
        if any(
            value is None
            for value in (
                item.impressions,
                item.clicks,
                item.favorites,
                item.sales,
                item.price,
                item.fees,
                item.refunds,
                item.conversion,
                item.net_revenue,
            )
        ):
            reasons.add("complete_economics_missing")
            continue
        if item.sales - item.refunds <= 0 or item.conversion <= 0:  # type: ignore[operator]
            reasons.add("real_sales_evidence_missing")
        if item.net_revenue <= 0:  # type: ignore[operator]
            reasons.add("positive_net_revenue_missing")
    ordered = tuple(
        reason
        for reason in (
            "complete_economics_missing",
            "real_sales_evidence_missing",
            "positive_net_revenue_missing",
        )
        if reason in reasons
    )
    return SkuExpansionVerdict(not ordered, ordered)
