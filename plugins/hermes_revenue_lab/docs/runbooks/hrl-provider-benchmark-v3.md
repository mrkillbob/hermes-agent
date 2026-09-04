# HRL v3 Provider, Effort, and Concurrency Benchmark Runbook

## Purpose and authority

Use this runbook to qualify Hermes model routes before changing profile defaults, fallbacks,
auxiliary models, or Kanban concurrency caps. The corpus is synthetic and the receipts are
`diagnostic-only`: they do not start LunaBot `main.py`, contact a broker, submit orders, or establish
runtime acceptance.

The 2026-08-31 reference receipts are under
`artifacts/provider_benchmarks/2026-08-31/`. They bind their results to the exact HRL v3 corpus
version and SHA-256. The operator-facing tune and rationale are also preserved in the active
LunaBot Vault note `Investigations/Hermes provider benchmark and routing tune 2026-08-31.md`.

## Required order of operations

1. Confirm the active HRL checkout, commit, Python environment, and clean/owned worktree changes.
2. Run deterministic corpus and validator tests before spending provider calls.
3. Run one smoke task against every discovered candidate.
4. Run the complete ten-task corpus only for smoke-qualified routes.
5. Reject any route that is not correctness-complete. Do not average away a failed strict task.
6. Test reasoning effort on strict business-analysis, coding-repair, synthesis, and audit tasks.
7. Test concurrency only after the route and effort pass correctness.
8. Apply routing changes through an exact profile inventory with backups and a post-write readback.
9. Re-run `hermes config check`, scan active config for retired models, and record the tune in the
   Vault. Keep failed/retired results as history, never as active candidates.

## Prerequisites

- The verified Hermes Python 3.13 environment is installed at
  `~/.hermes/hermes-agent/venv`. The HRL `.venv` does not carry Hermes provider SDKs and is not the
  benchmark runtime.
- Hermes source/runtime is available at `~/.hermes/hermes-agent`, or set
  `HERMES_AGENT_ROOT=/absolute/path/to/hermes-agent`.
- Provider credentials are resolved by Hermes from `~/.hermes/.env` and Codex auth state.
- The Hermes gateway and Luna runtime are stopped if their work would contaminate latency or local
  resource measurements.

## Deterministic preflight

```bash
cd /Users/mikedemott/HermesRevenueLab
PYTHONPATH=src:. /Users/mikedemott/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/test_model_corpus.py tests/test_model_validators.py
```

## Phase 1: provider smoke

Use a new output path. The executable defaults intentionally exclude retired GPT-5.4 and
GPT-5.4-mini models.

```bash
PYTHONPATH=src:.:/Users/mikedemott/.hermes/hermes-agent \
  /Users/mikedemott/.hermes/hermes-agent/venv/bin/python \
  scripts/run_cloud_provider_benchmarks.py \
  --smoke \
  --output artifacts/provider_benchmarks/candidate-smoke.json
```

For a newly released model, add it without editing the script:

```bash
PYTHONPATH=src:.:/Users/mikedemott/.hermes/hermes-agent \
  /Users/mikedemott/.hermes/hermes-agent/venv/bin/python \
  scripts/run_cloud_provider_benchmarks.py \
  --provider openai-codex \
  --model openai-codex=gpt-new-model \
  --smoke \
  --output artifacts/provider_benchmarks/gpt-new-model-smoke.json
```

## Phase 2: full correctness

Seed from passing smoke receipts so only qualified routes receive all ten tasks:

```bash
PYTHONPATH=src:.:/Users/mikedemott/.hermes/hermes-agent \
  /Users/mikedemott/.hermes/hermes-agent/venv/bin/python \
  scripts/run_cloud_provider_benchmarks.py \
  --seed artifacts/provider_benchmarks/candidate-smoke.json \
  --output artifacts/provider_benchmarks/candidate-full.json
```

Promotion requires every required task to have `status=completed`, `success=true`, and no failed
strict family. Transport timeouts and invalid structured responses are failures, not missing data.

## Phase 3: effort

The effort lane uses the four strict families. Test `low`, `medium`, and `high`; do not assume a
higher label is better or that one model's result transfers to another.

```bash
PYTHONPATH=src:.:/Users/mikedemott/.hermes/hermes-agent \
  /Users/mikedemott/.hermes/hermes-agent/venv/bin/python \
  scripts/run_codex_effort_concurrency.py \
  --model gpt-5.6-luna --model gpt-5.5 \
  --effort low --effort medium --effort high \
  --concurrency 1 --concurrency 2 --concurrency 4 \
  --output artifacts/provider_benchmarks/codex-effort-concurrency.json
```

## Phase 4: free-route concurrency

Only pass routes that were correctness-complete in Phase 2:

```bash
PYTHONPATH=src:.:/Users/mikedemott/.hermes/hermes-agent \
  /Users/mikedemott/.hermes/hermes-agent/venv/bin/python \
  scripts/run_free_route_concurrency.py \
  --route openrouter=thinkingmachines/inkling-small:free \
  --route nous=tencent/hy3:free \
  --concurrency 1 --concurrency 2 --concurrency 4 \
  --output artifacts/provider_benchmarks/free-route-concurrency.json
```

Treat a concurrency level as qualified only when every repeated strict task passes. Throughput is
secondary to correctness. Provider availability and rate limits can drift, so rerun this phase
before raising caps.

## 2026-08-31 reference result

The full ten-task run produced: Sol 10/10, Luna 10/10, GPT-5.5 9/10, Terra 9/10, Spark 8/10,
Nous HY3 10/10, OpenRouter Inkling Small 10/10, Solar 9/10, Stepfun 9/10, and Longcat 8/10.
OpenCode free candidates did not produce a promotion-complete result. Retired GPT-5.4 results remain
in the receipt only as historical evidence.

On the strict effort lane, Luna passed 4/4 at low, medium, and high. GPT-5.5 low passed 3/4 because
coding repair emitted unsafe Python; medium and high passed 4/4. Luna-low is therefore the default
general repair/audit route, while GPT-5.5-low is limited to bounded non-coding orchestration.

At concurrency four, both Luna-low and GPT-5.5-low completed the measured batch, but GPT-5.5's
single-lane and correctness variability keeps it out of coding repair. Inkling Small is the fast
light-work route; HY3 is a slower research/exploration route and not the final verification
authority.

## Migration and rollback

- Preserve the pre-change live config in a timestamped backup directory.
- Require the migration script's expected profile set to equal the live profile set exactly.
- Abort on unknown or unassigned profiles.
- After applying, re-read all profile YAML and summarize provider/model/effort counts.
- Verify zero active references to retired models and run `hermes config check`.
- If readback or config validation fails, restore only the files from that migration's exact backup;
  do not reset unrelated user configuration.

The 2026-08-31 live backup is
`~/.hermes/config-backups/benchmark-retune-20260831T075924Z`. The measured tune is 26 Inkling Small,
29 Luna-low, 7 GPT-5.5-low, 7 Sol-high, 7 Terra-medium, 9 HY3, and 4 qwen3.5:4b profiles, with
Kanban caps 4/4/4/2/2/2/1 respectively for GPT-5.5/Luna/Inkling/Sol/Terra/HY3/qwen.
