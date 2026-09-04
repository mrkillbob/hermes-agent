# HRL-6 Opportunity Scoring Engine

## Candidate contract

`OpportunityCandidate` contains each of the 21 required fields exactly once. Every field is either
`observed` with a bounded value or `unavailable` with `None`; both statuses require one or more raw
evidence IDs. Each evidence item retains its field, factual statement, source reference, and
timezone-aware observation time.

Candidate construction rejects missing/duplicate fields, duplicate evidence IDs, and references to
missing or differently scoped evidence. No unavailable value becomes zero or neutral.

## Assessment contract

The eight required dimensions are demand, monetizability, automation, competition, defensibility,
cost, risk, and time to revenue. Bands are `very_low`, `low`, `medium`, `high`, or `very_high`.
These are attractiveness bands: higher is more favorable. Every dimension cites evidence relevant
to its fixed source-field domain.

The ranking factors preserve the formula’s direction explicitly:

- numerator: expected value, automation, recurrence, and defensibility; and
- denominator: human labor, capital required, and platform risk.

The engine uses integer band weights and exact rational comparison internally. It publishes only
an A–E ranking tier and ordered opportunity IDs. There is deliberately no `ranking_score` field,
so an ordinal judgment cannot masquerade as measured precision.

## Verification

```bash
PYTHONPATH=src:. .venv/bin/python -m unittest tests.test_opportunity_scoring -v
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -q
PYTHONPATH=src:. .venv/bin/python -m compileall -q src scripts tests
```
