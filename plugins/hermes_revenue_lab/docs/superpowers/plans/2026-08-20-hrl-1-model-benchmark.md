# HRL-1 Local Model Benchmark Implementation Plan

**Goal:** Build a guarded, reproducible local benchmark whose measured evidence is the only input to
HRL-2 model selection.

**Architecture:** Immutable corpus/candidate/result types feed a direct streaming Ollama client.
A conservative preflight guard executes before every request. Deterministic validators score each
task, a selector chooses the smallest passing model, and an atomic publisher binds JSON, Markdown,
selections, and checksums.

**Tech stack:** Python 3.11 standard library, `unittest`, Ollama loopback HTTP API, JSON artifacts.

## Task 1: Benchmark evidence types and fixed corpus

- Create `src/hermes_revenue_lab/models/types.py`.
- Create `src/hermes_revenue_lab/models/corpus.py`.
- Test exact corpus counts, stable corpus digest, JSON schemas, and absence of network/customer data.
- Commit as `feat: define HRL benchmark corpus`.

## Task 2: Deterministic task validators

- Create `src/hermes_revenue_lab/models/validators.py`.
- Validate classification, extraction, deduplication, analysis fields, scoring, tool selection,
  synthesis citations, collector output, audit schema, and escalation decision.
- Preserve unavailable rubric dimensions as `null`.
- Commit as `feat: add deterministic benchmark validators`.

## Task 3: Benchmark guard

- Create `src/hermes_revenue_lab/models/benchmark_guard.py`.
- Deny all inference for active Luna, protected weekday time, unavailable resource evidence, memory
  pressure, swap pressure, excessive load, or an existing Revenue Lab model worker.
- Test that denied paths never invoke the supplied Ollama transport.
- Commit as `feat: guard HRL model benchmarks`.

## Task 4: Streaming Ollama client

- Create `src/hermes_revenue_lab/models/ollama_client.py`.
- Pin loopback, exact model, bounded timeout, `think: false`, and
  `chat_template_kwargs.enable_thinking: false` for non-thinking tasks.
- Measure first token and Ollama duration/token fields.
- Reject unexpected thinking output and never retry automatically across models.
- Commit as `feat: measure local Ollama inference`.

## Task 5: Benchmark orchestrator and selector

- Create `src/hermes_revenue_lab/models/benchmark.py`.
- Create `src/hermes_revenue_lab/models/selection.py`.
- Load candidates only from the certified inventory and bind names to digests.
- Record blocked/unavailable results; select only complete passing tiers.
- Commit as `feat: select models from measured evidence`.

## Task 6: Atomic publication and CLI

- Create `src/hermes_revenue_lab/models/publish.py`.
- Create `scripts/run_model_benchmarks.py`.
- Publish JSON, Markdown, selections, and SHA-256 manifest after secret-safety validation.
- A protected-time invocation must publish a truthful blocked run without calling Ollama.
- Commit as `feat: publish HRL model benchmark evidence`.

## Task 7: Live certification

- Run the full suite and protected-window zero-call proof.
- When the guard is `FULL`, benchmark installed 4B candidates first, then standard/reasoning/coding/
  escalation candidates one at a time. Never download a missing model.
- Recheck the guard between tasks and stop/checkpoint if it changes.
- Verify artifacts, tier availability, Ollama unload/ownership safety, and TradingBotV18 invariance.
- Write `docs/runbooks/hrl-1.md` and commit as `feat: certify HRL-1 model benchmarks`.
