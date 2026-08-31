# Desktop perf harness

One systematized way to measure desktop rendering/interaction performance,
diff it against a committed baseline, and fail on regressions. It replaces the
dozen one-off `measure-*` / `profile-*` scripts that each reinvented the CDP
client, arg parsing, stats, and output (and never had a baseline).

## Quick start

```bash
# Isolated instance (recommended) — no running app or LLM credits needed.
# Its own --user-data-dir + HERMES_HOME means it never collides with `hgui`.
npm run perf -- --spawn

# Or: launch an isolated instance once, attach repeatedly (faster iteration).
npm run perf:serve            # leaves an instance on :9222
npm run perf                  # attaches, runs the CI suite, gates on baseline

# One scenario, with a CPU profile:
npm run perf -- stream --cpuprofile --tokens 800

# Representative PRODUCTION numbers (minified React, not the ~3x-slower dev build):
npm run perf -- cold-start stream keystroke transcript --spawn --prod

# Re-capture the baseline on your reference device, then commit baseline.json:
npm run perf -- cold-start stream keystroke transcript --spawn --prod --update-baseline
```

## Lunar City packaged acceptance

Lunar City has a separate, fail-closed packaged path. It does not build a
package, use dev Electron, or treat a skipped Playwright test as evidence. The
operator must provide an existing clean electron-builder binary whose embedded
stamp matches the exact requested Git SHA, plus observed machine metadata:

```bash
npm run perf:lunar-city:accept -- \
  --binary /absolute/path/Hermes.app/Contents/MacOS/Hermes \
  --sha 0123456789abcdef0123456789abcdef01234567 \
  --metadata /absolute/path/lunar-city-machine.json \
  --output /absolute/path/lunar-city-receipts \
  --scenario balanced-overview
```

The optional metadata JSON is supplemental operator annotation only. It cannot
satisfy an acceptance gate. Architecture, hardware model, OS, power state,
Electron/Chromium versions, window bounds, display scale, GPU adapter, GPU
state, and scheduler FPS cap are captured from the launched package and host.
The command writes immutable `*.raw.json` and `*.receipt.json` files, SHA-256
binds the exact raw bytes and runtime environment into the receipt, then runs
the canonical validator. It exits nonzero if capture, GPU telemetry, raw
metrics, package provenance, scenario timing, receipt validation, or
validator-owned acceptance eligibility is unavailable.

`visible-idle` always measures at least 60 seconds. `30-minute-stability`
always measures at least 1,800,000 ms. Programmatic overrides may lengthen
these clocks or increase sampling frequency; they cannot shorten a scenario.
Unit tests use an injected clock and never stand in for these wall-clock runs.

Every exact-population scenario—25 active, 100 active, 250 LOD, and 30-minute
stability—requires canonical `lunar-city-population-v3` fixture bytes and three
gateways started and owned by the same run. The contract digest, raw bytes,
authenticated observed source mix, and observed subagent keys must agree.
Descriptor flags cannot claim subagent evidence. Fixture paths must be real,
current-UID-owned, non-symlinked descendants of a run-specific isolated root
with a nonce-bound ownership sentinel; real homes, workspaces, and broad
ancestors are refused. The runner passes only the verified Hermes home and
user-data directory and does not inherit API keys or tokens.

The repository currently has no supported lifecycle that both owns all three
fixture gateways and emits authenticated observed `subagent.start` evidence.
Therefore exact-population package acceptance remains deliberately blocked.
Profile/session/Kanban-backed preserved-population scenarios may still capture
diagnostics, but cannot become fake-backend acceptance unless their fixture
backend provenance is validated. This blocker is never reported as a skip or
acceptance.

`npm run perf:lunar-city` is raw capture only and deliberately emits no
validator-eligible evidence class. `npm run perf:lunar-city:validate --
<receipt.json>` validates an existing complete receipt. Supervised-live
orchestration is not exposed: it remains blocked until a real, supported
gateway preseed API exists and must never be inferred from a fake fixture.

## Dev vs prod

By default the harness measures the **dev** renderer (fast to spin up, good for
relative regression checks). Pass `--prod` (with `--spawn`) to build a
production renderer _with the probe included_ (`VITE_PERF_PROBE=1`) and measure
minified React — the representative shipped numbers. The committed baseline is
captured with `--prod`.

## Why isolation matters

The measurement this harness exists to run was historically blocked: a running
`hgui` holds the Electron single-instance lock, so a second instance quit
immediately. `--spawn` / `perf:serve` launch with their own `--user-data-dir`
(separate lock scope), their own `HERMES_HOME` (separate backend + sessions),
and their own `--remote-debugging-port`. Synthetic scenarios drive `$messages`
directly via `window.__PERF_DRIVE__`, so no LLM credits are spent.

## Scenarios

| scenario         | tier    | measures                                                                        | replaces                                                            |
| ---------------- | ------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `stream`         | ci      | streaming longtasks, frame p95/p99, mutation cadence                            | measure-synthetic-stream, profile-synth-stream, profile-long-stream |
| `stream --real`  | backend | same, from a real LLM stream                                                    | measure-real-stream, profile-real-stream                            |
| `keystroke`      | ci      | composer keystroke → paint latency                                              | measure-latency, profile-typing, leak-typing                        |
| `transcript`     | ci      | large-transcript mount + paint cost                                             | (new)                                                               |
| `render-churn`   | ci      | per-component render attribution + store churn while N tabs stream              | (new)                                                               |
| `idle-cost`      | report  | busy-but-silent tiles: idle commit rate, + fps while resizing / typing          | (new)                                                               |
| `right-pane`     | report  | file tree + persistent xterm tabs under chat/terminal output and split dragging | (new)                                                               |
| `cold-start`     | cold    | launch → CDP → driver → first paint (fresh spawn/run)                           | (new)                                                               |
| `first-token`    | backend | Enter → first assistant token painted (TTFT)                                    | (new)                                                               |
| `submit`         | backend | Enter → cleared → user msg painted, scroll jump                                 | measure-submit, measure-jump                                        |
| `session-switch` | backend | route → first-paint → settle                                                    | profile-session-switch                                              |
| `session-load`   | backend | how far a session's transcript moves after first paint                          | (new)                                                               |
| `profile-switch` | backend | rail click → sidebar settled                                                    | measure-profile-switch                                              |

`ci` + `cold` scenarios need no backend/credits and are gated against
`baseline.json` (`cold-start` requires `--spawn` since it measures a fresh
launch, and must be run in its own invocation). `backend` scenarios need a live
backend (and `--spawn` or a real session/credits) and are report-only.

CPU profiling is a cross-cutting `--cpuprofile` flag on any scenario (it wraps
the run in `Profiler.start/stop` and prints a top-self-time table), replacing
every standalone `profile-*` script.

## Adding a scenario

Create `scenarios/<name>.mjs` exporting `{ name, tier, description, run(cdp, opts) }`
where `run` returns `{ metrics, detail }` (metrics = flat numbers, lower is
better), then register it in `scenarios/index.mjs`. If it's `ci`, add a
`baseline.json` entry (or run `--update-baseline`).

## Layout

- `lib/cdp.mjs` — the one CDP client + target discovery + typing + CPU-profile wrapper + DOM selectors.
- `lib/stats.mjs` — percentiles, histograms, CPU-profile self-time ranking.
- `lib/baseline.mjs` — load/compare/update the baseline + regression gate.
- `lib/launch.mjs` — attach, or spawn a fully isolated instance.
- `scenarios/` — one module per measurement.
- `run.mjs` — entrypoint. `serve.mjs` — standalone isolated launcher.

## Not migrated (kept as dev utilities)

`eval.mjs`, `reload.mjs`, `reload-renderer.mjs`, `probe-renderer.mjs`,
`probe-thread.mjs`, `click-session.mjs`, `diag-*.mjs` are interactive dev
helpers, not benchmarks. They can adopt `lib/cdp.mjs` in a follow-up.
