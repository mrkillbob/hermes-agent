# HRL-18 Recommendation-Only Capital Allocator

## Decision contract

`recommend_capital_action` evaluates one strict `CapitalEvidence` record and returns exactly one
of `recommend_increase`, `hold`, `modify`, or `kill`.

An increase requires all of the following:

- positive known contribution margin;
- at least one real customer with retained customer evidence references;
- automation at or above its configured success threshold for the required stable windows;
- known green compliance;
- known human minutes per fulfillment within the declared acceptable threshold.

Negative contribution margin or non-green compliance returns `kill`. Known fixable automation or
human-burden failures return `modify`. Missing evidence, no real customers, or zero margin returns
`hold`.

## Authority boundary

Every `CapitalRecommendation` has `authority=recommendation_only`,
`requires_human_approval=true`, and `actual_spend_allowed=false`. The module has no payment,
marketplace, advertising, purchasing, or approval transport. It does not accept a requested budget
amount and therefore cannot accidentally convert a recommendation into a spend instruction.

Source and real-customer references are bounded and retained on the input evidence. Synthetic test
fixtures demonstrate branch behavior only; they do not satisfy the real-customer gate.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_capital_allocator.py
PYTHONPATH=src python3 -m pytest -q
```

The focused tests cover the eligible recommendation, no-spend invariant, missing customers,
negative margin, red compliance, unstable automation, excessive human burden, unknown evidence,
and strict input units.
