# HRL-20 Model-Routing Learning

## Observation dataset

`RoutingLearningStore` is a private, root-contained, write-once SQLite store. Each observation
retains:

- task type;
- exact provider, model, and model digest;
- wall-clock latency and model-compute seconds;
- success and final outcome;
- optional review score;
- retry count and escalation state;
- optional signed profit;
- source reference and timestamp.

This evidence is grouped by task type and exact model identity. Results from normalization cannot
be used as proof that the same model is suitable for automation code, and a tag with a different
digest is a different candidate.

## Optimization objective

The primary metric is useful outputs per wall-clock second. Success rate and known review score are
secondary tie-breakers, followed by lower total latency. A slower larger model that produces the
same useful result is therefore a regression. Profit per compute hour is reported only when every
observation in that exact group has known profit and the compute denominator is positive; partial
profit data stays unknown.

`recommend_task_routes` enforces a configurable minimum sample count for each task/model group.
Insufficient tasks return no model. Recommendations bind exact identity, are `advisory_only`, and
do not update `config/model_routing_policy.json`.

## Learning boundary

HRL-20 performs deterministic aggregation only. It does not fine-tune a model, rewrite prompts,
download candidates, change routing, or claim that benchmark prestige predicts business value.
The retained task/outcome dataset is the current learning asset.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_routing_learning.py
PYTHONPATH=src python3 -m pytest -q
```

Tests cover private write-once retention, exact identity grouping, throughput, profit unknowns, the
slow-model regression rule, task isolation, sample gates, and retention of review/retry/escalation
outcomes.
