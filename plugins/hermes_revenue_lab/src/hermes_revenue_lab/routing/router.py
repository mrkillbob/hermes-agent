"""Fail-closed task resolution and bounded route execution."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar, cast

from hermes_revenue_lab.deterministic.catalog import require_no_llm

from .types import (
    RouteDecision,
    RoutingEvent,
    RoutingPolicy,
    TaskExecutionReceipt,
    TIER_NAMES,
    TierName,
)


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_T = TypeVar("_T")


class TierUnavailableError(RuntimeError):
    """A requested route was denied before executor invocation."""

    def __init__(self, event: RoutingEvent):
        super().__init__(f"requested tier is unavailable: {event.task_result}")
        self.event = event


class ModelExecutionError(RuntimeError):
    """An executor exhausted bounded retries without exposing its error text."""

    def __init__(self, event: RoutingEvent):
        super().__init__("routed task failed after bounded retries")
        self.event = event


class ModelRouter:
    def __init__(
        self,
        policy: RoutingPolicy,
        *,
        event_sink: Callable[[RoutingEvent], None] | None = None,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        event_id_provider: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._policy = policy
        self._event_sink = event_sink
        self._utc_now = utc_now
        self._monotonic = monotonic
        self._event_id_provider = event_id_provider

    @staticmethod
    def _identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"{name} must be a bounded reason code")
        return value

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("routing clock must return timezone-aware UTC timestamps")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _compute_cost(wall_time_seconds: float) -> dict[str, object]:
        return {
            "basis": "measured_local_wall_time",
            "local_compute_seconds": wall_time_seconds,
            "monetary_cost": None,
            "electricity_cost": None,
        }

    def _denied_event(
        self,
        requested_tier: TierName,
        task_id: str,
        task_result: str,
        escalation_reason: str | None,
    ) -> RoutingEvent:
        observed = self._timestamp(self._utc_now())
        return RoutingEvent(
            event_id=self._identifier(self._event_id_provider(), "event id"),
            task_id=task_id,
            requested_tier=requested_tier,
            actual_tier=None,
            actual_model=None,
            model_digest=None,
            escalation_reason=escalation_reason,
            started_at=observed,
            ended_at=observed,
            wall_time_seconds=0.0,
            task_result=task_result,
            retries=0,
            estimated_compute_cost=self._compute_cost(0.0),
            success=False,
        )

    def resolve(
        self,
        requested_tier: str,
        task_id: str,
        *,
        luna_active: bool = False,
        escalation_reason: str | None = None,
        operation: str | None = None,
    ) -> RouteDecision:
        if requested_tier not in TIER_NAMES:
            raise ValueError(f"unknown requested tier {requested_tier}")
        tier = cast(TierName, requested_tier)
        task_id = self._identifier(task_id, "task id")
        if operation is not None:
            require_no_llm(operation, tier)
        if tier == "escalation":
            if escalation_reason is None:
                raise ValueError("escalation reason is required")
            escalation_reason = self._identifier(escalation_reason, "escalation reason code")
        elif escalation_reason is not None:
            escalation_reason = self._identifier(escalation_reason, "escalation reason code")
        selected = self._policy.tiers[tier]
        if selected.status != "available":
            raise TierUnavailableError(
                self._denied_event(tier, task_id, "unavailable", escalation_reason)
            )
        if luna_active and selected.model is not None:
            raise TierUnavailableError(
                self._denied_event(tier, task_id, "blocked_luna", escalation_reason)
            )
        if luna_active and not selected.permitted_during_luna:
            raise TierUnavailableError(
                self._denied_event(tier, task_id, "blocked_luna", escalation_reason)
            )
        return RouteDecision(
            requested_tier=tier,
            actual_tier=tier,
            actual_model=selected.model,
            model_digest=selected.model_digest,
            thinking=selected.thinking,
            reasoning=selected.reasoning,
            escalation_reason=escalation_reason,
        )

    def _emit(self, event: RoutingEvent) -> None:
        if self._event_sink is not None:
            self._event_sink(event)

    def execute(
        self,
        requested_tier: str,
        task_id: str,
        executor: Callable[[RouteDecision], TaskExecutionReceipt[_T]],
        *,
        luna_active: bool = False,
        escalation_reason: str | None = None,
        operation: str | None = None,
        max_retries: int = 0,
    ) -> tuple[_T, RoutingEvent]:
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or not 0 <= max_retries <= 2
        ):
            raise ValueError("max_retries must be an integer from zero through two")
        try:
            decision = self.resolve(
                requested_tier,
                task_id,
                luna_active=luna_active,
                escalation_reason=escalation_reason,
                operation=operation,
            )
        except TierUnavailableError as exc:
            self._emit(exc.event)
            raise
        started_at = self._timestamp(self._utc_now())
        started_monotonic = self._monotonic()
        last_error: Exception | None = None
        result: _T | None = None
        attempts = 0
        for attempts in range(1, max_retries + 2):
            try:
                receipt = executor(decision)
                if not isinstance(receipt, TaskExecutionReceipt):
                    raise RuntimeError("executor did not return an identity receipt")
                if (
                    receipt.actual_model != decision.actual_model
                    or receipt.model_digest != decision.model_digest
                ):
                    raise RuntimeError("executor identity receipt does not match route decision")
                result = receipt.value
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        ended_monotonic = self._monotonic()
        ended_at = self._timestamp(self._utc_now())
        wall_time = round(max(0.0, ended_monotonic - started_monotonic), 6)
        event = RoutingEvent(
            event_id=self._identifier(self._event_id_provider(), "event id"),
            task_id=self._identifier(task_id, "task id"),
            requested_tier=decision.requested_tier,
            actual_tier=decision.actual_tier,
            actual_model=decision.actual_model,
            model_digest=decision.model_digest,
            escalation_reason=decision.escalation_reason,
            started_at=started_at,
            ended_at=ended_at,
            wall_time_seconds=wall_time,
            task_result="failed" if last_error is not None else "succeeded",
            retries=attempts - 1,
            estimated_compute_cost=self._compute_cost(wall_time),
            success=last_error is None,
        )
        self._emit(event)
        if last_error is not None:
            raise ModelExecutionError(event) from last_error
        return cast(_T, result), event
