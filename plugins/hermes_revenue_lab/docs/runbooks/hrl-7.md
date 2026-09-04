# HRL-7 Scout System

## Boundary

The scout package validates already-collected public facts. It performs no browsing, outreach,
customer contact, purchase, or model invocation. A candidate holds at most 64 evidence facts; each
fact retains a public HTTP source, permission basis, source class, timezone-aware collection time,
content SHA-256, fact code, and bounded value.

## Eligibility

- Business Problem Scout requires an objective diagnostic such as a broken conversion path,
  measured performance/mobile failure, stale information, public reputation metric, competitor
  disadvantage, or missing public functionality. `llm_opinion` alone is ineligible.
- Data Opportunity Scout requires evidence of fragmented sources, repeated updates, economic
  usefulness, and historical-dataset value.
- Alert Opportunity Scout requires a qualifying public event, monetary value from early notice,
  and at least one authoritative public or public-API source.
- Digital Product Scout requires a narrow calculator/spreadsheet/template/planning/reference/utility
  type and demonstrable demand. Generic AI art is explicitly rejected.

## Retention

`ScoutStore` saves both accepted and rejected candidates in a root-contained mode-`0600` SQLite
database. The store recomputes the deterministic verdict before writing, refuses forged verdicts,
and preserves every source fact. Duplicate candidate/evidence IDs fail rather than overwrite.

## Verification

```bash
PYTHONPATH=src:. .venv/bin/python -m unittest tests.test_scouts -v
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -q
PYTHONPATH=src:. .venv/bin/python -m compileall -q src scripts tests
```
