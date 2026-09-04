# HRL-5 Revenue Ledger

## Outcome

The ledger stores one current snapshot for every experiment plus immutable audit events, raw
promotion evidence, and archive findings. The database must live beneath an explicit allowed root,
is mode `0600`, and refuses a symlink target. No network or model call is involved.

## Accounting contract

`ExperimentRecord` tracks identity, status, all revenue/cost/resource/funnel fields, timestamps, and
verdict. Money and duration values are finite nonnegative `Decimal` values. Counts are nonnegative
integers. Unknown measurements are `None` in Python and `NULL` in SQLite; they are never silently
converted to zero.

Updates require the current integer revision and a strictly advancing `updated_at`. A stale writer
gets `ledger revision conflict`. Create, update, evidence, and archive operations commit their audit
event in the same SQLite transaction.

## Derived metric definitions

- net revenue = gross revenue - refunds - platform fees - payment fees;
- contribution profit = net revenue - advertising - API - other - electricity cost;
- contribution margin = contribution profit / net revenue;
- CAC = advertising cost / customers;
- conversion rate = conversions / visitors;
- revenue per lead/customer uses net revenue;
- ROCH and ROHH divide contribution profit by observed compute and human hours; and
- profit per day/month annualizes contribution profit over the snapshot observation interval, using
  30.4375 days per average month.

A composite is `None` if any required input is unknown. A zero denominator is also undefined.

## Promotion ladder

`highest_promotion_stage(record, evidence)` evaluates the ladder sequentially:

- E1 requires `market_test_live` evidence;
- E2 requires a positive `legitimate_customer_payment` whose evidenced relationship is `stranger`;
- E3 requires at least $50 cumulative gross revenue;
- E4 requires at least $100 gross revenue and positive contribution profit;
- E5 requires at least $250 evidenced monthly revenue and less than 120 human minutes/month;
- E6 requires at least $500 evidenced monthly recurring revenue; and
- E7 requires explicit stable-unit-economics evidence.

Only the fixed evidence codebook is accepted. `ai_opinion` is invalid and cannot create a promotion.

## Kill and archive behavior

`archive_experiment` requires one or more bounded reason codes, a narrative finding, an advancing
timestamp, and the current revision. It sets status to `archived`, records a categorical killed
verdict, and retains all accounting, promotion evidence, and findings. It does not automatically
delete or rediscover the experiment.

## Verification

```bash
PYTHONPATH=src:. .venv/bin/python -m unittest tests.test_revenue_ledger -v
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -q
PYTHONPATH=src:. .venv/bin/python -m compileall -q src scripts tests
```
