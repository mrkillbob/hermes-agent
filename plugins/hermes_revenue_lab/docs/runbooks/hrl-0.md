# HRL-0 Environment Foundation Runbook

## Terminal classification

HRL-0 is **infrastructure-valid** at inventory
`20260820T112051Z-b1b27c347a23`.

The inventory's resource classification is separately and truthfully `observed_busy`. Its
three-sample quiet window exceeded the idle threshold, so the idle baseline remains unavailable.
That is an operating observation, not an isolation or publication failure, and no threshold was
weakened to make the patch pass.

Acceptance evidence:

- all required inventory sections are available;
- canonical JSON, Markdown, command manifest, and Desktop verdict share one inventory identity;
- all four payload checksums verify;
- the sensitive-label publication scan passes;
- the macOS process sandbox permits lab writes and denies a write-only open in TradingBotV18;
- the TradingBotV18 probe file hash and dirty-status hash stayed unchanged;
- Hermes Desktop registered `Hermes Revenue Lab` at `http://127.0.0.1:9120` as a non-primary
  remote gateway and its app-level Test reported `Reachable`;
- the standalone smoke receives anonymous HTTP 401 and authenticated HTTP 200 from the protected,
  read-only `/api/config` route; it does not call chat, generation, or model endpoints;
- the final smoke left the Ollama process-list hash unchanged;
- the temporary backend was stopped and port 9120 was confirmed closed.

The TradingBotV18 read-only preflight and postflight were identical:

- branch: `codex/phase12-publishable-build`
- HEAD: `5951744b44101238b0388e04274e7301d46b4799`
- status-text SHA-256: `156ace687c9c3216a90be16da43761a7a3d820a447a33e5d27e45a9b3d1da279`

## Secret rules

The lab-only dashboard credential lives at `.hermes/.env`, which is mode `0600` and Git-ignored.
The initializer accepts exactly one `HERMES_DASHBOARD_SESSION_TOKEN` entry, preserves it across
runs, and never returns or prints it. Do not use `cat`, shell tracing, screenshots, clipboard
history, or artifact serialization on this file. Enter it only into Hermes Desktop's secure
session-token field.

Canonical artifacts reject keys or labeled strings associated with credentials, authorization,
cookies, hardware serials, or hardware UUIDs. Discovery causes publication rejection; it is never
converted into a placeholder and silently published.

## Regenerate inventory

From `/Users/mikedemott/HermesRevenueLab`:

```bash
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src:. .venv/bin/python scripts/collect_environment_inventory.py
```

Inventory collection performs no Ollama inference. It uses allowlisted argv-only commands,
samples resource state three times, runs the non-mutating isolation proof, and atomically replaces
canonical artifacts only after publication-safety validation. Regeneration intentionally resets
the Desktop verdict to `not_observed`; rerun the backend and Desktop checks below to bind fresh
connection evidence to the new inventory identity.

## Start and test Hermes Desktop

First confirm no existing listener owns the dedicated endpoint:

```bash
/usr/sbin/lsof -nP -iTCP:9120 -sTCP:LISTEN
```

No output is expected. An occupied port is a fail-closed condition; do not choose another port or
terminate an unknown process.

Initialize and start the lab backend:

```bash
PYTHONPATH=src:. .venv/bin/python scripts/init_lab_runtime.py
scripts/hermes-revenue-lab
```

In another terminal, run the non-inference smoke:

```bash
PYTHONPATH=src:. .venv/bin/python scripts/desktop_smoke.py
```

In Hermes Desktop, open **Settings → Gateways**, locate **Hermes Revenue Lab**, and click
**Test**. The saved connection is machine-level, non-primary, and uses the stable encrypted token
entered during HRL-0. The app Test verifies HTTP reachability and its authenticated WebSocket
connection. Record only status, timestamp, gateway name, and endpoint; never record the token.

## Stop and prove shutdown

Press `Control-C` in the terminal that owns `scripts/hermes-revenue-lab`. Do not use a broad
`pkill` pattern.

```bash
/usr/sbin/lsof -nP -iTCP:9120 -sTCP:LISTEN
```

No output is expected. Leave the registered Desktop gateway offline and non-primary. Persistent
startup, scheduling, and unattended workers remain deferred to HRL-4.

## Resource observation caveat

Ollama was empty immediately after the first backend smoke. A `qwen3:4b-instruct` load was observed
later, after the Desktop UI step, with unknown ownership. It was not unloaded or signaled. The
isolated HRL logs contained no inference marker, and a subsequent final protected-route smoke
produced identical before/after Ollama process-list hashes. The HRL-0 conclusion is therefore
limited to: the certified smoke did not change observed Ollama state. It does not claim ownership
of concurrent local inference activity.
