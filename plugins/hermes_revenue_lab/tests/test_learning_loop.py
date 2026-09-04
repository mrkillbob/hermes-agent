from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from hermes_revenue_lab.learning import (
    ExperimentForecast,
    ExperimentOutcome,
    LearningStore,
    calibrate_outcomes,
)

START = "2026-08-01T00:00:00+00:00"
END = "2026-08-08T00:00:00+00:00"
OBSERVED = "2026-08-09T00:00:00+00:00"


def forecast(forecast_id: str = "forecast-001") -> ExperimentForecast:
    return ExperimentForecast(
        forecast_id=forecast_id,
        experiment_id="experiment-001",
        window_start=START,
        window_end=END,
        predicted_demand=Decimal(10),
        predicted_conversion=Decimal("0.10"),
        predicted_price=Decimal(20),
        predicted_automation=Decimal("0.80"),
        predicted_profit=Decimal(100),
        recorded_at=START,
    )


def outcome(forecast_id: str = "forecast-001") -> ExperimentOutcome:
    return ExperimentOutcome(
        forecast_id=forecast_id,
        observed_at=OBSERVED,
        actual_demand=Decimal(8),
        actual_conversion=Decimal("0.08"),
        actual_willingness_to_pay=Decimal(18),
        actual_automation=Decimal("0.75"),
        actual_human_intervention_minutes=Decimal(30),
        actual_profit=Decimal(70),
        source_refs=("ledger:experiment-001", "artifact:run-001/verdict.json"),
    )


class LearningLoopTest(unittest.TestCase):
    def test_forecast_and_actual_units_are_strict_and_unknowns_preserved(self) -> None:
        self.assertEqual(Decimal("0.80"), forecast().predicted_automation)
        unknown = ExperimentOutcome(
            forecast_id="forecast-unknown",
            observed_at=OBSERVED,
            actual_demand=None,
            actual_conversion=None,
            actual_willingness_to_pay=None,
            actual_automation=None,
            actual_human_intervention_minutes=None,
            actual_profit=None,
            source_refs=("artifact:run-unknown/verdict.json",),
        )
        self.assertIsNone(unknown.actual_profit)
        with self.assertRaisesRegex(ValueError, "ratio"):
            forecast().__class__(
                **{**forecast().__dict__, "predicted_conversion": Decimal("1.1")}
            )

    def test_store_is_private_root_contained_and_write_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LearningStore(
                root / "private" / "learning.sqlite3", allowed_root=root
            )
            store.record_forecast(forecast())
            store.record_outcome(outcome())
            self.assertEqual(((forecast(), outcome()),), store.completed_windows())
            self.assertEqual(0o600, store.database.stat().st_mode & 0o777)
            with self.assertRaisesRegex(ValueError, "already exists"):
                store.record_forecast(forecast())
            with self.assertRaisesRegex(ValueError, "already exists"):
                store.record_outcome(outcome())

            outside = Path(directory) / "outside.sqlite3"
            with self.assertRaisesRegex(ValueError, "outside"):
                LearningStore(outside, allowed_root=root / "private")

    def test_outcome_must_follow_its_forecast_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LearningStore(root / "learning.sqlite3", allowed_root=root)
            store.record_forecast(forecast())
            with self.assertRaisesRegex(ValueError, "after forecast window"):
                store.record_outcome(
                    ExperimentOutcome(**{**outcome().__dict__, "observed_at": START})
                )

    def test_deterministic_calibration_preserves_per_dimension_error_and_bias(
        self,
    ) -> None:
        result = calibrate_outcomes(((forecast(), outcome()),))
        self.assertEqual(1, result.sample_size)
        self.assertEqual(Decimal(2), result.mean_absolute_error["demand"])
        self.assertEqual(Decimal("0.02"), result.mean_absolute_error["conversion"])
        self.assertEqual(Decimal(2), result.mean_absolute_error["price"])
        self.assertEqual(Decimal("0.05"), result.mean_absolute_error["automation"])
        self.assertEqual(Decimal(30), result.mean_absolute_error["profit"])
        self.assertEqual(Decimal(-30), result.mean_bias["profit"])
        self.assertEqual("deterministic_statistical", result.method)

    def test_incomplete_actuals_do_not_become_zero_error(self) -> None:
        incomplete = ExperimentOutcome(
            **{
                **outcome().__dict__,
                "actual_willingness_to_pay": None,
                "actual_profit": None,
            }
        )
        result = calibrate_outcomes(((forecast(), incomplete),))
        self.assertIsNone(result.mean_absolute_error["price"])
        self.assertIsNone(result.mean_absolute_error["profit"])
        self.assertEqual(0, result.observation_counts["price"])


if __name__ == "__main__":
    unittest.main()
