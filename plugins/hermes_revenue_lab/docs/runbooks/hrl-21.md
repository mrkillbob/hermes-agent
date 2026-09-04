# HRL-21 Governed First-Run Sequence

## Controller outcome

`build_first_run_plan` is the final no-mutation composition boundary. It requires exactly one
source-bound check for each of:

- model routing;
- the canonical Luna resource guard;
- the Hermes cron fleet;
- sealed artifacts and provenance;
- browser availability;
- the revenue ledger;
- opportunity scoring.

Any failed check blocks before candidate or review logic. Every returned `FirstRunPlan` is
`dry_run=true` and keeps publishing, spending, customer outreach, and experiment launch false.

## Scout and review gates

The plan requires at least 20 unique evidence-bound candidate receipts. Candidate fixtures in the
test suite are structural evidence only and are not a real scout result. Real candidates must come
from HRL-7 scout evidence and HRL-6 ranking receipts.

An authenticated human receipt must cover the complete candidate set, provide rationale references,
and approve exactly three unique eligible candidates: one B2B opportunity/audit, one niche
intelligence/data candidate, and one digital product. A model review cannot substitute for the
human receipt. Successful composition returns nominations only; each nomination still says
`requires_fresh_approval=true` and `launch_allowed=false`.

## Luna priority integration

HRL-21 consumes the real HRL-4 guard decision rather than recreating Luna policy. The integration
test injects both an active Luna process and an already-running Revenue worker representing queued
or in-flight heavy work. The canonical guard returns `PAUSED`, and the first-run controller blocks.
The watchdog's checkpoint/stop-receipt contract remains the only interruption path for a registered
Revenue worker; HRL-21 has no process-control path.

## Current acceptance classification

The controller and no-mutation contract are acceptance-valid at the code level. A real scout,
human review, and three experiment nominations are deliberately not fabricated by this patch.
Until those evidence inputs exist, operational first-run status is `blocked_scout_count` or
`awaiting_human_review`, not a logic regression. Hermes Desktop and its gateway remain stopped, so
the installed cron fleet is not automatically firing.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_first_run.py
PYTHONPATH=src python3 -m pytest -q
```

The focused tests cover all seven subsystem checks, the 20-candidate minimum, mandatory complete
human review, exactly three lane-separated nominations, immutable no-mutation flags, and real guard
blocking for artificial Luna-active and Revenue-worker-active states.
