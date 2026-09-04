# HRL-19 Cost Accounting

## Compute estimate

Local inference has a cost, but rough wattage is not a meter reading. `ComputeAssumptions` retains:

- active model-compute seconds;
- configurable low and high watts;
- configurable USD per kWh;
- the measurement or estimation basis;
- the electricity-price source.

`estimate_costs` reports `estimated_compute_cost_low_usd`,
`estimated_compute_cost_high_usd`, and the midpoint `estimated_compute_cost_usd`. Its precision is
always `estimate_interval`; the midpoint must not be represented as an exact utility charge.

## Complete cost categories

Every estimate must provide exactly one entry for platform fees, hosting, domains, APIs,
marketplace fees, payment fees, refunds, and advertising. Amounts are nonnegative exact Decimals or
explicitly unknown. Source references are mandatory and secret-shaped text is rejected.

When every non-compute amount is known, the estimated total is a low/high range. If any category is
unknown, the total remains unknown and both total endpoints remain `None`; only the sum of known
costs plus low-end compute is exposed as `known_cost_lower_bound_usd`. An unknown category is never
treated as zero.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_cost_accounting.py
PYTHONPATH=src python3 -m pytest -q
```

The focused tests cover interval arithmetic, assumption retention, all eight required categories,
unknown propagation, known lower bounds, duplicate/missing categories, invalid power ranges, and
secret-shaped source rejection.
