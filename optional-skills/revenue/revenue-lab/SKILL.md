---
name: revenue-lab
description: Operate Hermes Revenue Lab with guarded local workflows.
version: 0.2.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Revenue, Experiments, Governance, Local]
    category: revenue
---

# Hermes Revenue Lab

Use this skill when the user asks Hermes to inspect, repair, or reason about Hermes Revenue Lab.

Hermes Revenue Lab (HRL) used to be a separate repo (`mrkillbob/HermesRevenueLab`) resolved from
an external checkout root. It has been folded in-tree: its code now lives at
`plugins/hermes_revenue_lab/` (own `src/`, `scripts/`, `tests/`, `docs/`, `config/`, `README.md`,
`AGENTS.md`) inside the hermes-agent repo. There is no external checkout, no
`integrations.revenue_lab.root` config key, and no `HERMES_REVENUE_LAB_ROOT` env var anymore —
everything resolves relative to the plugin directory itself.

Keep these boundaries (unchanged from HRL's own operating rules — see
`plugins/hermes_revenue_lab/AGENTS.md`):

- Revenue Lab is local-first and yields to Luna.
- Default to private drafts, dry runs, simulations, and read-only collection.
- Never publish, spend, advertise, contact customers, create accounts, accept terms, enter
  contracts, issue refunds, handle sensitive customer data, or subscribe to services without the
  deterministic compliance boundary and an authenticated, unexpired approval where required.
- Never weaken the resource governor, compliance registry, approval policy, provenance checks, or
  model identity checks to make a test pass.
- Read `plugins/hermes_revenue_lab/README.md`, `AGENTS.md`, and the relevant
  `plugins/hermes_revenue_lab/docs/runbooks/hrl-*.md` before changing HRL behavior.
- Treat HRL evidence as diagnostic-only unless the runbook says otherwise and the current command
  output proves it.
- Do not weaken the resource governor, approval policy, compliance registry, provenance checks,
  corpus checksum, or model identity checks to pass tests.

## Invoking the in-tree plugin

Prefer the bundled `hermes revenue-lab` CLI bridge (registered by
`plugins/hermes_revenue_lab/plugin.yaml`) over calling scripts by hand:

```bash
hermes revenue-lab status                                  # checkout-free status + entrypoints, as JSON
hermes revenue-lab preflight                                # deterministic corpus/validator tests
hermes revenue-lab guard --workload guard_check             # evaluate the revenue guard
```

For anything the CLI bridge doesn't cover, run HRL's own scripts directly from the repo root, with
both HRL's `src/` and the hermes-agent repo root on `PYTHONPATH` (HRL scripts import hermes-agent
modules like `agent.auxiliary_client` and `hermes_cli.auth` directly):

```bash
PYTHONPATH=plugins/hermes_revenue_lab/src:. python3 plugins/hermes_revenue_lab/scripts/revenue_guard.py --workload guard_check
PYTHONPATH=plugins/hermes_revenue_lab/src:. python3 -m pytest -q plugins/hermes_revenue_lab/tests/
```

For provider benchmark work, follow `plugins/hermes_revenue_lab/docs/runbooks/hrl-provider-benchmark-v3.md`.
Provider, effort, and concurrency receipts are diagnostic-only.

Do not reintroduce an external-checkout indirection (env var, config root, separate clone) for
this integration — HRL's source of truth is now the in-tree plugin, developed only in hermes-agent
going forward.
