# HRL-4 Luna Resource Governor Design

## Scope

`revenue_guard` is the canonical admission and interruption authority for Revenue Lab work. It
combines live process, protected-window, CPU/load, memory, swap, safe loopback Luna-health, loaded
model, and active Revenue worker evidence into `FULL`, `LIMITED`, `PAUSED`, or `EMERGENCY_STOP`.

## Policy

Missing required host evidence is `PAUSED`. Any governed Luna/TradingBot process or the
conservative weekday 05:45–13:30 Pacific window is `PAUSED` for all Revenue Lab workloads. Wall
clock can pause a job but can never prove Luna inactive. Critical memory/load or growing swap under
low memory is `EMERGENCY_STOP`. Reduced memory, elevated load, or historical swap is `LIMITED`;
only deterministic work and model candidates below 12B may start. One active Revenue worker
prevents parallel launch.

Safe Luna health is a bounded loopback diagnostic. It never overrides process evidence and an
unavailable endpoint is not treated as healthy or inactive.

## Worker ownership and interruption

Long-running Revenue workers register a private record containing workload ID, PID, process start
token, workload class, and root-contained checkpoint path. Before signalling, the watchdog re-reads
the PID start token to defeat PID reuse and acts only on matching Revenue records. It writes a
checkpoint request receipt before sending `SIGTERM`. It never uses broad process matching, never
signals an unregistered process, and contains no Luna stop/restart path.

Long work is decomposed into checkpoints. The guard runs before launch and between steps. A changed
decision stops new steps, persists the current checkpoint, records reason codes, and returns a
structured blocked result.

## Acceptance

Tests prove state precedence, active-Luna dominance outside clock windows, protected-window pause,
limited workload classes, no parallel workers, missing-evidence closure, health subordination,
targeted process discovery beyond bounded full-table truncation, periodic checkpoint behavior, PID
start-token ownership, exact SIGTERM targeting, and absence of any Luna termination path.
