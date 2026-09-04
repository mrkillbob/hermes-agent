# HRL-3 Zero-LLM Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make deterministic Revenue Lab work execute through bounded scripts and skip Hermes agent/model invocation when interpretation is unnecessary.

**Architecture:** A fixed catalog declares model-ineligible tasks. Pure standard-library operations implement the listed deterministic work, while a stateful precheck engine renders Hermes' installed `wakeAgent` JSON contract without raw content.

**Tech Stack:** Python 3.11 standard library, `Decimal`, read-only SQLite, loopback HTTP, JSON, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-21-hrl-3-zero-llm-design.md`

## Global Constraints

- Every cataloged deterministic operation requires the `no_llm` tier.
- No Ollama, model client, Hermes agent, prompt, or provider import is permitted.
- Inputs, outputs, rows, and context are bounded; unknown evidence fails closed.
- Cron stdout ends in canonical `wakeAgent` JSON; no-change emits `false`.
- Cron fleet creation and unattended activation remain deferred to HRL-14.

---

### Task 1: Deterministic task catalog and operations

**Files:**
- Create: `src/hermes_revenue_lab/deterministic/__init__.py`
- Create: `src/hermes_revenue_lab/deterministic/catalog.py`
- Create: `src/hermes_revenue_lab/deterministic/operations.py`
- Test: `tests/test_zero_llm_operations.py`

**Interfaces:**
- Produces: `DETERMINISTIC_OPERATIONS`, `require_no_llm(operation, tier)`, bounded hash,
  timestamp, dedupe, decimal, SQLite, metrics, health, system, schedule, and threshold functions.

- [ ] Write failing tests for every catalog entry and each fail-closed boundary.
- [ ] Run the focused module and confirm missing imports fail.
- [ ] Implement the smallest typed standard-library operations satisfying the tests.
- [ ] Rerun the focused module and require all cases to pass.

### Task 2: Stateful Hermes precheck contract

**Files:**
- Create: `src/hermes_revenue_lab/deterministic/precheck.py`
- Create: `scripts/zero_llm_precheck.py`
- Test: `tests/test_zero_llm_precheck.py`

**Interfaces:**
- Consumes: bounded JSON configuration and state under an allowed root.
- Produces: `PrecheckDecision`, `evaluate_precheck(config, allowed_root)`, and canonical last-line
  Hermes JSON.

- [ ] Write failing tests for unchanged, changed, fully handled, unsafe path, malformed config, and
  canonical `wakeAgent` rendering.
- [ ] Run the focused module and confirm missing implementation failures.
- [ ] Implement file-digest and threshold gates with atomic private state updates.
- [ ] Implement the generic script entrypoint with nonzero exit on unsafe/unknown evidence.
- [ ] Rerun the focused module and require all cases to pass.

### Task 3: Certification and handoff

**Files:**
- Modify: `README.md`
- Create: `docs/runbooks/hrl-3.md`

**Interfaces:**
- Produces: safe manual/precheck commands and the explicit HRL-14 installation boundary.

- [ ] Document catalog operations, gate output, config/state boundaries, and deferred scheduling.
- [ ] Run the full suite, syntax compilation, `git diff --check`, and an import scan proving no
  model client exists under `deterministic/`.
- [ ] Verify Hermes remains stopped, Ollama remains empty, and TradingBotV18 invariance holds.
- [ ] Stage only HRL-3 files and commit as `feat: add HRL-3 zero-LLM execution`.
