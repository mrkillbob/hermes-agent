# HRL-17 Deterministic Learning Loop

## Outcome

HRL-17 records an immutable forecast before an experiment window and one immutable outcome only
after the window ends. `LearningStore` uses a private, root-contained SQLite file and reconstructs
strict typed values rather than treating the database as an unvalidated source.

The retained pairs cover:

| Forecast | Actual |
|---|---|
| Demand | Demand |
| Conversion ratio | Conversion ratio |
| Price | Willingness to pay |
| Automation ratio | Automation ratio plus human-intervention minutes |
| Signed profit | Signed profit |

Actual values may be unavailable. SQL `NULL` and Python `None` preserve that state; calibration
excludes it only for the affected dimension. Unknown is never coerced to zero.

## Calibration boundary

`calibrate_outcomes` calculates deterministic per-dimension observation counts, mean absolute
error, and signed mean bias. The result is descriptive evidence for a later governed ranking
change. It does not fine-tune a model, change a routing policy, or mutate an opportunity score.

Every outcome requires bounded source references so an error statistic is traceable to the ledger
or sealed run artifact that supplied the actual value. Forecast IDs, experiment IDs, timestamps,
ratios, Decimal amounts, and source references are all validated before persistence.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_learning_loop.py
PYTHONPATH=src python3 -m pytest -q
```

The tests cover strict units, explicit unknowns, private root containment, write-once forecasts and
outcomes, post-window enforcement, deterministic error/bias, and per-dimension missing-data
behavior. Synthetic fixtures prove the contract only; they are not business learning evidence.
