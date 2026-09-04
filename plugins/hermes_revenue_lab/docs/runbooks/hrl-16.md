# HRL-16 Revenue Dashboard

## URL and operating mode

```text
http://127.0.0.1:9131
```

Start it with:

```bash
scripts/run_revenue_dashboard.py --port 9131
```

Without `--snapshot`, the dashboard deliberately renders every source-backed value as
`Unavailable`. To supply data, pass a bounded, non-symlink JSON document conforming to
`hrl.dashboard_snapshot.v1` inside `/Users/mikedemott/HermesRevenueLab`.

The server binds only `127.0.0.1`. It exposes `GET /`, `GET /api/snapshot`, and `GET /health`.
POST, PUT, PATCH, and DELETE all return `405 read_only`. Responses disable caching, framing,
content sniffing, referrers, forms, scripts, and external resources. There are no brokerage,
trading, publishing, spending, or other mutation controls.

## Panels

- Today: revenue, expenses, signed profit, customers, compute hours, human intervention.
- Experiments: researching, testing, profitable, scaling, killed.
- Model economics: exact model identity, invocations, median latency, success, escalation, compute
  time.
- Luna guard: FULL/LIMITED/PAUSED/EMERGENCY_STOP, reason, last transition, and current bounded
  resource state.
- Opportunity queue: candidate, score, evidence count, proposed experiment, required approval.

Unknown values remain `None` in the snapshot and `Unavailable` in the UI. They are never displayed
as zero. Profit may be negative and must reconcile exactly with known revenue and expenses.

## Visual source of truth and fidelity ledger

- Concept: `docs/design/hrl-16-dashboard-concept.png`
- Verified desktop render: `docs/design/hrl-16-dashboard-render.png`
- Verified 390x844 mobile render: `docs/design/hrl-16-dashboard-mobile.png`

The visual system uses the concept’s graphite/navy field, narrow status rail, amber unavailable
state, teal healthy state, red paused/killed state, open metric rails, and table-driven lower panels.

| Comparison point | Result |
|---|---|
| Header copy and hierarchy | Exact title and `Local evidence only`; unavailable data time is no longer implied current |
| Panel order | Today, experiments, model economics/Luna guard, opportunity queue match |
| Palette | Graphite/navy, cool gray, amber, teal, and red match without gradients or glows |
| Container model | Status rail plus open bands/tables; no card grid or marketing shell |
| Empty-state density | Five table rows and dash-plus-Unavailable metric treatment preserve scan rhythm |
| Responsive behavior | Rail hides below 980px; metric rails collapse; data tables scroll horizontally |

The first browser render exposed an ISO timestamp overflow and a false implication that snapshot
generation time was source-data time. Both were fixed before the retained screenshots. Browser
console verification reported zero warnings and zero errors. The only intentional implementation
deviation is dependency-free semantic HTML/CSS served by Python’s standard library instead of
React/Vite; this preserves the repository’s zero-runtime-dependency boundary and the accepted
screen faithfully.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_revenue_dashboard.py
PYTHONPATH=src python3 -m pytest -q
scripts/run_revenue_dashboard.py --check
```

Playwright fallback was used because the in-app browser controls were unavailable in this
continuation. The retained render was captured at the concept’s 1440x1000 viewport, and mobile was
checked at 390x844. Both the concept and final render were inspected directly with `view_image`.
