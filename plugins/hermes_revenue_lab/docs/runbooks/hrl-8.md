# HRL-8 Experiment A: B2B Opportunity Intelligence

## Selected vertical

The one initial vertical is independent HVAC contractors in Sacramento County, California. This is
an experiment selection, not a validated market claim. A production batch must contain 80–120
unique public-source business targets and 10–20 unique high/very-high-confidence findings.

## Evidence and audit output

Every promoted finding must belong to a target inside the cohort, pass the objective Business
Problem Scout, and cite only evidence owned by that target. Sample audits expose the exact problem,
evidence IDs, consequence, practical remedy, and optional competitor comparison. Their status is
`sample_not_customer_contact`.

The four price records remain hypotheses: $49 basic diagnostic, $99 detailed audit, $149–299
competitor audit, and $49–199 monthly monitoring. HRL-8 does not claim these prices work; later
market tests must measure them.

## Safety and evidence status

There is no email, messaging, outreach, purchasing, mass-spam, or impersonation transport in this
patch. Unit fixtures prove cohort and schema enforcement but are not real businesses or acceptance
evidence. Public cohort collection may run only when `revenue_guard` permits the workload.

## Verification

```bash
PYTHONPATH=src:. .venv/bin/python -m unittest tests.test_experiment_a -v
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -q
```
