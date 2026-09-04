"""Deterministic exact economics for HRL-5 experiment snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .types import ExperimentRecord, parse_timestamp


@dataclass(frozen=True)
class DerivedMetrics:
    net_revenue: Decimal | None
    contribution_profit: Decimal | None
    contribution_margin: Decimal | None
    cac: Decimal | None
    conversion_rate: Decimal | None
    revenue_per_lead: Decimal | None
    revenue_per_customer: Decimal | None
    roch: Decimal | None
    rohh: Decimal | None
    profit_per_day: Decimal | None
    profit_per_month: Decimal | None


def _ratio(numerator: Decimal | None, denominator: Decimal | int | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / Decimal(denominator)


def derive_metrics(record: ExperimentRecord) -> DerivedMetrics:
    transaction_values = (
        record.gross_revenue,
        record.refunds,
        record.platform_fees,
        record.payment_fees,
    )
    net_revenue = None
    if all(value is not None for value in transaction_values):
        gross, refunds, platform, payment = transaction_values
        assert gross is not None and refunds is not None
        assert platform is not None and payment is not None
        net_revenue = gross - refunds - platform - payment

    contribution_values = transaction_values + (
        record.advertising_cost,
        record.api_cost,
        record.other_cost,
        record.electricity_estimate,
    )
    contribution_profit = None
    if all(value is not None for value in contribution_values):
        assert record.gross_revenue is not None
        contribution_profit = record.gross_revenue - sum(
            (value for value in contribution_values[1:] if value is not None),
            start=Decimal("0"),
        )

    compute_hours = (
        None if record.compute_seconds is None else record.compute_seconds / Decimal("3600")
    )
    human_hours = None if record.human_minutes is None else record.human_minutes / Decimal("60")
    elapsed = parse_timestamp(record.updated_at) - parse_timestamp(record.created_at)
    elapsed_seconds = Decimal(str(elapsed.total_seconds()))
    elapsed_days = elapsed_seconds / Decimal("86400") if elapsed_seconds > 0 else None
    profit_per_day = _ratio(contribution_profit, elapsed_days)
    return DerivedMetrics(
        net_revenue=net_revenue,
        contribution_profit=contribution_profit,
        contribution_margin=_ratio(contribution_profit, net_revenue),
        cac=_ratio(record.advertising_cost, record.customers),
        conversion_rate=_ratio(
            None if record.conversions is None else Decimal(record.conversions),
            record.visitors,
        ),
        revenue_per_lead=_ratio(net_revenue, record.leads),
        revenue_per_customer=_ratio(net_revenue, record.customers),
        roch=_ratio(contribution_profit, compute_hours),
        rohh=_ratio(contribution_profit, human_hours),
        profit_per_day=profit_per_day,
        profit_per_month=(
            None if profit_per_day is None else profit_per_day * Decimal("30.4375")
        ),
    )
