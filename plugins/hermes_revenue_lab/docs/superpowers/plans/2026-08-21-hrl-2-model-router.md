# HRL-2 Authoritative Local Model Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one fail-closed, checksum-bound task router whose model choices come only from HRL-1 evidence and whose executions emit bounded metadata.

**Architecture:** A deterministic policy builder verifies HRL-1 artifacts and derives the fixed six-tier policy. An immutable router resolves requests, enforces Luna and escalation controls, invokes an injected executor with bounded same-model retries, and emits secret-safe events to a private append-only ledger.

**Tech Stack:** Python 3.11 standard library, immutable dataclasses, JSON/SHA-256, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-21-hrl-2-model-router-design.md`

## Global Constraints

- Exact model names and digests come only from verified `artifacts/model_benchmarks/model_selections.json`.
- Missing, failed, stale, or tampered evidence remains unavailable; there is no fallback model.
- Active Luna denies all model-backed routes; `no_llm` may remain available.
- Retry count is at most two and retries never change tier or model.
- No prompt, raw result, exception message, secret, or customer content enters policy or event artifacts.
- Runtime ledger writes remain under `/Users/mikedemott/HermesRevenueLab` and use mode `0600`.

---

### Task 1: Immutable router types and verified policy derivation

**Files:**
- Create: `src/hermes_revenue_lab/routing/__init__.py`
- Create: `src/hermes_revenue_lab/routing/types.py`
- Create: `src/hermes_revenue_lab/routing/policy.py`
- Test: `tests/test_router_policy.py`

**Interfaces:**
- Consumes: HRL-1 benchmark, selections, and checksum manifest paths.
- Produces: `TierPolicy`, `RoutingPolicy`, `PolicyIntegrityError`,
  `derive_policy_document(selections)`, and `load_verified_policy(...)`.

- [ ] **Step 1: Write failing policy tests** for exact tier derivation, checksum tampering,
  benchmark-binding mismatch, policy drift, unavailable-tier model rejection, and deterministic
  serialization.
- [ ] **Step 2: Run**
  `PYTHONPATH=src:. .venv/bin/python -m unittest tests.test_router_policy -v` and confirm the missing
  routing module fails.
- [ ] **Step 3: Implement immutable types** with the exact six-tier set and reject unknown fields,
  missing digests, or a model on an unavailable tier.
- [ ] **Step 4: Implement verification** by parsing the checksum manifest, hashing all HRL-1
  payloads, validating benchmark/inventory bindings, deriving policy controls, and comparing the
  loaded policy to the derived document.
- [ ] **Step 5: Rerun the focused module** and require all cases to pass.

### Task 2: Fail-closed routing and bounded execution events

**Files:**
- Create: `src/hermes_revenue_lab/routing/router.py`
- Test: `tests/test_model_router.py`

**Interfaces:**
- Consumes: `RoutingPolicy` and an injected
  `Callable[[RouteDecision], TaskExecutionReceipt[object]]` executor.
- Produces: `ModelRouter.resolve(...) -> RouteDecision` and
  `ModelRouter.execute(...) -> tuple[object, RoutingEvent]`.

- [ ] **Step 1: Write failing routing tests** proving fast resolves to the HRL-1 winner, unavailable
  tiers never invoke the executor, Luna blocks model-backed work, `no_llm` works while Luna is
  active, escalation requires a reason code, retries stay on one model, and failure messages are not
  recorded.
- [ ] **Step 2: Run**
  `PYTHONPATH=src:. .venv/bin/python -m unittest tests.test_model_router -v` and confirm the missing
  router fails.
- [ ] **Step 3: Implement resolution** with restricted task/reason identifiers and no tier fallback.
- [ ] **Step 4: Implement execution** with zero-to-two retries, injected UTC/monotonic clocks,
  an exact model/digest receipt check, categorical results, and estimated cost shaped as
  `{"basis":"measured_local_wall_time","local_compute_seconds":N,"monetary_cost":null,"electricity_cost":null}`.
- [ ] **Step 5: Rerun the focused module** and require all cases to pass.

### Task 3: Private append-only ledger and canonical policy CLI

**Files:**
- Create: `src/hermes_revenue_lab/routing/ledger.py`
- Create: `scripts/build_model_routing_policy.py`
- Create: `config/model_routing_policy.json`
- Modify: `.gitignore`
- Test: `tests/test_router_ledger.py`
- Test: `tests/test_router_policy_cli.py`

**Interfaces:**
- Consumes: `RoutingEvent.canonical_record()` and verified HRL-1 artifact paths.
- Produces: `append_routing_event(path, event, allowed_root)` and a deterministic policy generator.

- [ ] **Step 1: Write failing tests** for `0600` JSONL append, path escape, symlink refusal, secret
  rejection, deterministic CLI output, and refusal to overwrite policy from invalid evidence.
- [ ] **Step 2: Run the two focused modules** and confirm failures cite missing ledger/CLI behavior.
- [ ] **Step 3: Implement ledger writes** with `O_APPEND|O_CREAT|O_WRONLY`, `O_NOFOLLOW` where
  available, canonical one-line JSON, `fsync`, and root containment checks.
- [ ] **Step 4: Implement policy generation** using only `derive_policy_document` after full HRL-1
  verification, with atomic replacement and no timestamp.
- [ ] **Step 5: Generate** `config/model_routing_policy.json`, rerun the focused tests, regenerate it,
  and prove the file hash is unchanged.

### Task 4: Acceptance documentation and certification

**Files:**
- Modify: `README.md`
- Create: `docs/runbooks/hrl-2.md`

**Interfaces:**
- Consumes: canonical policy, focused test evidence, and runtime safety checks.
- Produces: operator commands and truthful tier availability for HRL-3.

- [ ] **Step 1: Document** the authoritative policy path, available tiers, rejection behavior,
  runtime ledger path, and regeneration/verification commands.
- [ ] **Step 2: Run** the complete suite with
  `PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -v`.
- [ ] **Step 3: Run** `git diff --check`, regenerate and compare the policy hash, verify HRL-1
  checksums, verify Ollama has no loaded model, and verify the Hermes gateway remains stopped.
- [ ] **Step 4: Re-prove** TradingBotV18 HEAD, branch, and working-tree fingerprint invariance.
- [ ] **Step 5: Stage** only HRL-2 files and commit as
  `feat: add authoritative HRL-2 model router`.
