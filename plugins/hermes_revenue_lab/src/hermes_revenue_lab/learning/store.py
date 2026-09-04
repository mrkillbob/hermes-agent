"""HRL-17 immutable forecast/outcome retention and deterministic calibration."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from hermes_revenue_lab.ledger.types import parse_timestamp

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_DIMENSIONS = ("demand", "conversion", "price", "automation", "profit")


def _identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is invalid")


def _decimal(
    name: str, value: object, *, nonnegative: bool, optional: bool = False
) -> None:
    if optional and value is None:
        return
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or (nonnegative and value < 0)
    ):
        qualifier = "nonnegative " if nonnegative else "finite "
        raise ValueError(
            f"{name} must be a {qualifier}Decimal" + (" or unknown" if optional else "")
        )


def _ratio(name: str, value: object, *, optional: bool = False) -> None:
    _decimal(name, value, nonnegative=True, optional=optional)
    if value is not None and value > 1:
        raise ValueError(f"{name} ratio must be between zero and one")


@dataclass(frozen=True)
class ExperimentForecast:
    forecast_id: str
    experiment_id: str
    window_start: str
    window_end: str
    predicted_demand: Decimal
    predicted_conversion: Decimal
    predicted_price: Decimal
    predicted_automation: Decimal
    predicted_profit: Decimal
    recorded_at: str

    def __post_init__(self) -> None:
        _identifier("forecast_id", self.forecast_id)
        _identifier("experiment_id", self.experiment_id)
        start = parse_timestamp(self.window_start)
        end = parse_timestamp(self.window_end)
        recorded = parse_timestamp(self.recorded_at)
        if end <= start or recorded > start:
            raise ValueError(
                "forecast must be recorded before a positive experiment window"
            )
        _decimal("predicted demand", self.predicted_demand, nonnegative=True)
        _ratio("predicted conversion", self.predicted_conversion)
        _decimal("predicted price", self.predicted_price, nonnegative=True)
        _ratio("predicted automation", self.predicted_automation)
        _decimal("predicted profit", self.predicted_profit, nonnegative=False)


@dataclass(frozen=True)
class ExperimentOutcome:
    forecast_id: str
    observed_at: str
    actual_demand: Decimal | None
    actual_conversion: Decimal | None
    actual_willingness_to_pay: Decimal | None
    actual_automation: Decimal | None
    actual_human_intervention_minutes: Decimal | None
    actual_profit: Decimal | None
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier("forecast_id", self.forecast_id)
        parse_timestamp(self.observed_at)
        _decimal("actual demand", self.actual_demand, nonnegative=True, optional=True)
        _ratio("actual conversion", self.actual_conversion, optional=True)
        _decimal(
            "actual willingness to pay",
            self.actual_willingness_to_pay,
            nonnegative=True,
            optional=True,
        )
        _ratio("actual automation", self.actual_automation, optional=True)
        _decimal(
            "actual human intervention",
            self.actual_human_intervention_minutes,
            nonnegative=True,
            optional=True,
        )
        _decimal("actual profit", self.actual_profit, nonnegative=False, optional=True)
        if not self.source_refs or any(
            not isinstance(item, str) or not 1 <= len(item) <= 1_000
            for item in self.source_refs
        ):
            raise ValueError("outcome requires bounded source references")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("outcome source references must be unique")


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class LearningStore:
    def __init__(self, database: Path, *, allowed_root: Path) -> None:
        root = allowed_root.resolve(strict=True)
        resolved = database.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("learning database is outside the allowed root") from exc
        if database.is_symlink():
            raise ValueError("learning database cannot be a symlink")
        resolved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        resolved.parent.chmod(0o700)
        self.database = resolved
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS forecasts (
                    forecast_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                    window_start TEXT NOT NULL, window_end TEXT NOT NULL,
                    predicted_demand TEXT NOT NULL, predicted_conversion TEXT NOT NULL,
                    predicted_price TEXT NOT NULL, predicted_automation TEXT NOT NULL,
                    predicted_profit TEXT NOT NULL, recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outcomes (
                    forecast_id TEXT PRIMARY KEY REFERENCES forecasts(forecast_id),
                    observed_at TEXT NOT NULL, actual_demand TEXT NULL,
                    actual_conversion TEXT NULL, actual_willingness_to_pay TEXT NULL,
                    actual_automation TEXT NULL, actual_human_intervention_minutes TEXT NULL,
                    actual_profit TEXT NULL, source_refs_json TEXT NOT NULL
                );
                """
            )
        self.database.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def record_forecast(self, value: ExperimentForecast) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        value.forecast_id,
                        value.experiment_id,
                        value.window_start,
                        value.window_end,
                        str(value.predicted_demand),
                        str(value.predicted_conversion),
                        str(value.predicted_price),
                        str(value.predicted_automation),
                        str(value.predicted_profit),
                        value.recorded_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("forecast already exists") from exc

    def record_outcome(self, value: ExperimentOutcome) -> None:
        with self._connect() as connection:
            forecast = connection.execute(
                "SELECT window_end FROM forecasts WHERE forecast_id = ?",
                (value.forecast_id,),
            ).fetchone()
            if forecast is None:
                raise KeyError(value.forecast_id)
            if parse_timestamp(value.observed_at) < parse_timestamp(
                forecast["window_end"]
            ):
                raise ValueError("outcome must be observed after forecast window")
            try:
                connection.execute(
                    "INSERT INTO outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        value.forecast_id,
                        value.observed_at,
                        _text(value.actual_demand),
                        _text(value.actual_conversion),
                        _text(value.actual_willingness_to_pay),
                        _text(value.actual_automation),
                        _text(value.actual_human_intervention_minutes),
                        _text(value.actual_profit),
                        json.dumps(value.source_refs, separators=(",", ":")),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("outcome already exists") from exc

    def completed_windows(
        self,
    ) -> tuple[tuple[ExperimentForecast, ExperimentOutcome], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM forecasts JOIN outcomes USING (forecast_id) ORDER BY window_end, forecast_id"
            ).fetchall()
        return tuple((_forecast(row), _outcome(row)) for row in rows)


def _forecast(row: sqlite3.Row) -> ExperimentForecast:
    return ExperimentForecast(
        forecast_id=row["forecast_id"],
        experiment_id=row["experiment_id"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        predicted_demand=Decimal(row["predicted_demand"]),
        predicted_conversion=Decimal(row["predicted_conversion"]),
        predicted_price=Decimal(row["predicted_price"]),
        predicted_automation=Decimal(row["predicted_automation"]),
        predicted_profit=Decimal(row["predicted_profit"]),
        recorded_at=row["recorded_at"],
    )


def _outcome(row: sqlite3.Row) -> ExperimentOutcome:
    return ExperimentOutcome(
        forecast_id=row["forecast_id"],
        observed_at=row["observed_at"],
        actual_demand=_nullable(row["actual_demand"]),
        actual_conversion=_nullable(row["actual_conversion"]),
        actual_willingness_to_pay=_nullable(row["actual_willingness_to_pay"]),
        actual_automation=_nullable(row["actual_automation"]),
        actual_human_intervention_minutes=_nullable(
            row["actual_human_intervention_minutes"]
        ),
        actual_profit=_nullable(row["actual_profit"]),
        source_refs=tuple(json.loads(row["source_refs_json"])),
    )


def _nullable(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


@dataclass(frozen=True)
class CalibrationResult:
    sample_size: int
    observation_counts: Mapping[str, int]
    mean_absolute_error: Mapping[str, Decimal | None]
    mean_bias: Mapping[str, Decimal | None]
    method: str = "deterministic_statistical"


def calibrate_outcomes(
    windows: Sequence[tuple[ExperimentForecast, ExperimentOutcome]],
) -> CalibrationResult:
    errors: dict[str, list[Decimal]] = {name: [] for name in _DIMENSIONS}
    biases: dict[str, list[Decimal]] = {name: [] for name in _DIMENSIONS}
    pairs = {
        "demand": ("predicted_demand", "actual_demand"),
        "conversion": ("predicted_conversion", "actual_conversion"),
        "price": ("predicted_price", "actual_willingness_to_pay"),
        "automation": ("predicted_automation", "actual_automation"),
        "profit": ("predicted_profit", "actual_profit"),
    }
    for forecast, outcome in windows:
        if forecast.forecast_id != outcome.forecast_id:
            raise ValueError("calibration window IDs do not match")
        for dimension, (predicted_name, actual_name) in pairs.items():
            predicted = getattr(forecast, predicted_name)
            actual = getattr(outcome, actual_name)
            if actual is not None:
                bias = actual - predicted
                biases[dimension].append(bias)
                errors[dimension].append(abs(bias))

    def means(values: Mapping[str, list[Decimal]]) -> Mapping[str, Decimal | None]:
        return MappingProxyType(
            {
                name: None
                if not items
                else sum(items, Decimal(0)) / Decimal(len(items))
                for name, items in values.items()
            }
        )

    return CalibrationResult(
        sample_size=len(windows),
        observation_counts=MappingProxyType(
            {name: len(items) for name, items in errors.items()}
        ),
        mean_absolute_error=means(errors),
        mean_bias=means(biases),
    )
