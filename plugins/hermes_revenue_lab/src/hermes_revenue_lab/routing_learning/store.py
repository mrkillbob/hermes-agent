"""Immutable observations and deterministic HRL-20 routing analysis."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from hermes_revenue_lab.ledger.types import parse_timestamp

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_DIGEST = re.compile(r"[A-Fa-f0-9]{12,128}")
_OUTCOMES = ("useful", "rejected", "failed")


def _identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is invalid")


def _decimal(
    name: str,
    value: object,
    *,
    optional: bool = False,
    nonnegative: bool = True,
) -> None:
    if optional and value is None:
        return
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or (nonnegative and value < 0)
    ):
        raise ValueError(
            f"{name} must be a finite Decimal" + (" or unknown" if optional else "")
        )


@dataclass(frozen=True)
class ModelObservation:
    observation_id: str
    task_type: str
    provider: str
    model: str
    model_digest: str
    latency_seconds: Decimal
    compute_seconds: Decimal
    success: bool
    review_score: Decimal | None
    retries: int
    escalated: bool
    final_outcome: Literal["useful", "rejected", "failed"]
    profit_usd: Decimal | None
    source_ref: str
    recorded_at: str

    def __post_init__(self) -> None:
        for name in ("observation_id", "task_type", "provider"):
            _identifier(name, getattr(self, name))
        if not isinstance(self.model, str) or not 1 <= len(self.model) <= 200:
            raise ValueError("model is invalid")
        if not isinstance(self.model_digest, str) or not _DIGEST.fullmatch(
            self.model_digest
        ):
            raise ValueError("model digest is invalid")
        _decimal("latency seconds", self.latency_seconds)
        _decimal("compute seconds", self.compute_seconds)
        if self.latency_seconds <= 0:
            raise ValueError("latency seconds must be positive")
        if type(self.success) is not bool or type(self.escalated) is not bool:
            raise ValueError("success and escalation must be booleans")
        _decimal("review score", self.review_score, optional=True)
        if self.review_score is not None and self.review_score > 1:
            raise ValueError("review score must be between zero and one")
        if type(self.retries) is not int or self.retries < 0:
            raise ValueError("retries must be a nonnegative integer")
        if self.final_outcome not in _OUTCOMES:
            raise ValueError("final outcome is invalid")
        if self.final_outcome == "useful" and not self.success:
            raise ValueError("useful outcome requires success")
        _decimal("profit", self.profit_usd, optional=True, nonnegative=False)
        if (
            not isinstance(self.source_ref, str)
            or not 1 <= len(self.source_ref) <= 1_000
        ):
            raise ValueError("source reference is invalid")
        parse_timestamp(self.recorded_at)


class RoutingLearningStore:
    def __init__(self, database: Path, *, allowed_root: Path) -> None:
        root = allowed_root.resolve(strict=True)
        resolved = database.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "routing learning database is outside the allowed root"
            ) from exc
        if database.is_symlink():
            raise ValueError("routing learning database cannot be a symlink")
        resolved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        resolved.parent.chmod(0o700)
        self.database = resolved
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY, task_type TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, model_digest TEXT NOT NULL,
                    latency_seconds TEXT NOT NULL, compute_seconds TEXT NOT NULL,
                    success INTEGER NOT NULL, review_score TEXT NULL, retries INTEGER NOT NULL,
                    escalated INTEGER NOT NULL, final_outcome TEXT NOT NULL,
                    profit_usd TEXT NULL, source_ref TEXT NOT NULL, recorded_at TEXT NOT NULL
                )
                """
            )
        self.database.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def record(self, value: ModelObservation) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        value.observation_id,
                        value.task_type,
                        value.provider,
                        value.model,
                        value.model_digest,
                        str(value.latency_seconds),
                        str(value.compute_seconds),
                        int(value.success),
                        None if value.review_score is None else str(value.review_score),
                        value.retries,
                        int(value.escalated),
                        value.final_outcome,
                        None if value.profit_usd is None else str(value.profit_usd),
                        value.source_ref,
                        value.recorded_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("model observation already exists") from exc

    def observations(self) -> tuple[ModelObservation, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM observations ORDER BY recorded_at, observation_id"
            ).fetchall()
        return tuple(_observation(row) for row in rows)


def _nullable_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _observation(row: sqlite3.Row) -> ModelObservation:
    return ModelObservation(
        observation_id=row["observation_id"],
        task_type=row["task_type"],
        provider=row["provider"],
        model=row["model"],
        model_digest=row["model_digest"],
        latency_seconds=Decimal(row["latency_seconds"]),
        compute_seconds=Decimal(row["compute_seconds"]),
        success=bool(row["success"]),
        review_score=_nullable_decimal(row["review_score"]),
        retries=row["retries"],
        escalated=bool(row["escalated"]),
        final_outcome=row["final_outcome"],
        profit_usd=_nullable_decimal(row["profit_usd"]),
        source_ref=row["source_ref"],
        recorded_at=row["recorded_at"],
    )


@dataclass(frozen=True)
class ModelPerformanceSummary:
    task_type: str
    provider: str
    model: str
    model_digest: str
    sample_size: int
    useful_output_count: int
    total_wall_clock_seconds: Decimal
    useful_outputs_per_wall_clock_second: Decimal
    success_rate: Decimal
    review_observation_count: int
    mean_review_score: Decimal | None
    retry_rate: Decimal
    escalation_rate: Decimal
    profit_observation_count: int
    profit_per_compute_hour_usd: Decimal | None

    @property
    def model_identity(self) -> tuple[str, str, str]:
        return (self.provider, self.model, self.model_digest)


def summarize_model_observations(
    observations: Sequence[ModelObservation],
) -> tuple[ModelPerformanceSummary, ...]:
    groups: dict[tuple[str, str, str, str], list[ModelObservation]] = defaultdict(list)
    for item in observations:
        groups[(item.task_type, item.provider, item.model, item.model_digest)].append(
            item
        )

    results: list[ModelPerformanceSummary] = []
    for (task_type, provider, model, digest), rows in sorted(groups.items()):
        count = len(rows)
        wall = sum((item.latency_seconds for item in rows), Decimal(0))
        useful = sum(item.success and item.final_outcome == "useful" for item in rows)
        reviews = [item.review_score for item in rows if item.review_score is not None]
        known_profits = [
            item.profit_usd for item in rows if item.profit_usd is not None
        ]
        compute = sum((item.compute_seconds for item in rows), Decimal(0))
        all_profit_known = len(known_profits) == count
        results.append(
            ModelPerformanceSummary(
                task_type=task_type,
                provider=provider,
                model=model,
                model_digest=digest,
                sample_size=count,
                useful_output_count=useful,
                total_wall_clock_seconds=wall,
                useful_outputs_per_wall_clock_second=Decimal(useful) / wall,
                success_rate=Decimal(sum(item.success for item in rows))
                / Decimal(count),
                review_observation_count=len(reviews),
                mean_review_score=(
                    None
                    if not reviews
                    else sum(reviews, Decimal(0)) / Decimal(len(reviews))
                ),
                retry_rate=Decimal(sum(item.retries > 0 for item in rows))
                / Decimal(count),
                escalation_rate=Decimal(sum(item.escalated for item in rows))
                / Decimal(count),
                profit_observation_count=len(known_profits),
                profit_per_compute_hour_usd=(
                    sum(known_profits, Decimal(0)) * Decimal(3600) / compute
                    if all_profit_known and compute > 0
                    else None
                ),
            )
        )
    return tuple(results)


@dataclass(frozen=True)
class RoutingRecommendation:
    task_type: str
    status: Literal["recommended", "insufficient_evidence"]
    provider: str | None
    model: str | None
    model_digest: str | None
    sample_size: int
    objective: str = "useful_output_per_wall_clock_second"
    authority: str = "advisory_only"


def recommend_task_routes(
    observations: Sequence[ModelObservation],
    *,
    minimum_samples: int,
) -> tuple[RoutingRecommendation, ...]:
    if type(minimum_samples) is not int or minimum_samples < 1:
        raise ValueError("minimum samples must be a positive integer")
    summaries = summarize_model_observations(observations)
    tasks = sorted({item.task_type for item in observations})
    results: list[RoutingRecommendation] = []
    for task in tasks:
        eligible = [
            item
            for item in summaries
            if item.task_type == task and item.sample_size >= minimum_samples
        ]
        if not eligible:
            results.append(
                RoutingRecommendation(
                    task_type=task,
                    status="insufficient_evidence",
                    provider=None,
                    model=None,
                    model_digest=None,
                    sample_size=max(
                        (
                            item.sample_size
                            for item in summaries
                            if item.task_type == task
                        ),
                        default=0,
                    ),
                )
            )
            continue
        winner = max(
            eligible,
            key=lambda item: (
                item.useful_outputs_per_wall_clock_second,
                item.success_rate,
                item.mean_review_score
                if item.mean_review_score is not None
                else Decimal(-1),
                -item.total_wall_clock_seconds,
            ),
        )
        results.append(
            RoutingRecommendation(
                task_type=task,
                status="recommended",
                provider=winner.provider,
                model=winner.model,
                model_digest=winner.model_digest,
                sample_size=winner.sample_size,
            )
        )
    return tuple(results)
