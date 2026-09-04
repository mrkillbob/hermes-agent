# HRL-4 Luna Resource Governor

## Outcome

HRL-4 is the canonical admission boundary for Revenue Lab work. It observes bounded host evidence,
evaluates an immutable workload request, and returns one of `FULL`, `LIMITED`, `PAUSED`, or
`EMERGENCY_STOP`. It does not start, stop, or restart Luna.

## Check-only operator entry point

```bash
PYTHONPATH=src:. .venv/bin/python scripts/revenue_guard.py \
  --workload guard_check
```

The command prints only a sanitized snapshot and decision. Exit code `0` means the requested
workload is permitted; exit code `3` means it is blocked. Model workloads require an explicit
positive parameter count, for example `--workload fast_model --parameters 4.7`. The optional
`--previous-swap-used-bytes` value enables growth detection between checks. The CLI never enforces
termination.

## State precedence

1. Missing process, load, CPU, memory-pressure, swap, loaded-model, or worker evidence is `PAUSED`.
2. Free memory below 10%, one-minute load above 1.5 times CPU count, or critical swap under low
   memory is `EMERGENCY_STOP`.
3. Any active Luna process, weekday 05:45–13:30 Pacific protected window, active Revenue worker,
   or unhealthy Luna diagnostic is `PAUSED`.
4. Free memory below 35%, load above 0.75 times CPU count, swap above 10%, or a foreign loaded model
   is `LIMITED`.
5. Otherwise the state is `FULL`.

`guard_check` remains permitted under pressure so monitoring can continue. `LIMITED` permits
deterministic work and, only when no foreign model is loaded, a fast model below 12B parameters.
Active Luna always pauses Revenue work even outside the protected window. Wall clock never proves
Luna inactive. The loopback health probe at `127.0.0.1:8787/health` is diagnostic and cannot
override process evidence.

## Long-running workers

Every long-running worker must use `run_guarded_steps` to re-evaluate the guard before every step
and persist a checkpoint after every completed step or block. A heavy worker eligible for emergency
interruption must also register a private `.worker.json` record beneath the Revenue Lab root with:

- workload ID and class;
- exact PID and `/bin/ps` process-start token;
- heavy-work flag;
- root-contained checkpoint path; and
- registration time.

`enforce_emergency_stop` acts only in `EMERGENCY_STOP`, revalidates the PID start token, requires an
existing bounded checkpoint, writes a `.stop.json` receipt, and then sends exact `SIGTERM` to that
one registered heavy worker. It never uses broad process matching and never signals Luna.

## Verification

```bash
PYTHONPATH=src:. .venv/bin/python -m unittest \
  tests.test_revenue_guard tests.test_revenue_watchdog -v
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src:. .venv/bin/python -m compileall -q src scripts tests
```

Acceptance requires Hermes Desktop/gateway stopped, unchanged TradingBotV18
HEAD/branch/status fingerprint, clean diff checks, and a source scan confirming that the guard
contains no broad or Luna termination path. An empty Ollama runtime is required only when Luna is
inactive. If Luna is active, its model occupancy is protected evidence and the required result is a
`PAUSED` Revenue decision; operators must not unload or terminate it.
