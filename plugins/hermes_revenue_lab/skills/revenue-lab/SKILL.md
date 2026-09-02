---
name: revenue-lab
description: Operate Hermes Revenue Lab from Hermes without bypassing its guarded local-first boundaries.
---

# Hermes Revenue Lab

Use this skill when the user asks Hermes to inspect, run, repair, or reason about Hermes Revenue Lab.

Hermes Revenue Lab is an external local-first checkout, not Hermes core. Default root resolution is:

1. `HERMES_REVENUE_LAB_ROOT`
2. `~/HermesRevenueLab`

Keep these boundaries:

- Start by running `hermes revenue-lab status` and confirming the HRL branch, HEAD, dirty state, and origin.
- Read HRL `AGENTS.md`, `README.md`, and the relevant `docs/runbooks/hrl-*.md` before changing HRL behavior.
- Treat HRL evidence as diagnostic-only unless the runbook says otherwise and the current command output proves it.
- Do not publish, spend, contact customers, create accounts, enter contracts, or mutate external services from this skill.
- Do not copy HRL source into Hermes core. Use the HRL checkout and its scripts as the authority.
- Do not weaken the resource governor, approval policy, compliance registry, provenance checks, corpus checksum, or model identity checks to pass tests.

Useful commands:

```bash
hermes revenue-lab status
hermes revenue-lab preflight
hermes revenue-lab guard --workload guard_check
```

For provider benchmark work, follow the HRL provider benchmark runbook in the HRL checkout. Provider, effort, and concurrency receipts are diagnostic-only.
