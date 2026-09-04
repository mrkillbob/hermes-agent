# HRL-4 Luna Resource Governor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the canonical admission and emergency-stop boundary that always yields Revenue Lab
resources to Luna.

**Architecture:** Immutable snapshots and workload specs feed a deterministic state evaluator. A
private start-token-bound worker registry and step watchdog checkpoint Revenue work and signal only
verified Revenue-owned heavy processes.

**Tech Stack:** Python 3.11 standard library, macOS allowlisted process/resource commands, loopback
HTTP, JSON, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-21-hrl-4-revenue-guard-design.md`

## Global Constraints

- Active Luna is sufficient to pause all Revenue Lab workloads regardless of wall clock.
- Missing required evidence fails closed.
- No broad kill command and no Luna stop/restart function may exist.
- Only PID plus matching process start token proves Revenue worker ownership.
- Checkpoint request is persisted before exact `SIGTERM`.

---

### Task 1: Canonical snapshot and state policy

**Files:**
- Create: `src/hermes_revenue_lab/guard/__init__.py`
- Create: `src/hermes_revenue_lab/guard/policy.py`
- Create: `src/hermes_revenue_lab/guard/collector.py`
- Test: `tests/test_revenue_guard.py`

- [x] Write failing state-precedence, Luna, window, pressure, workload, and health tests.
- [x] Implement immutable `RevenueSnapshot`, `WorkloadSpec`, and `GuardDecision` types.
- [x] Implement bounded collection and deterministic evaluation.
- [x] Rerun focused tests and require all cases to pass.

### Task 2: Registered-worker watchdog and checkpoints

**Files:**
- Create: `src/hermes_revenue_lab/guard/workers.py`
- Create: `src/hermes_revenue_lab/guard/watchdog.py`
- Create: `scripts/revenue_guard.py`
- Test: `tests/test_revenue_watchdog.py`

- [x] Write failing registry containment, PID reuse, exact-signal, checkpoint-first, and
  periodic-step tests.
- [x] Implement private atomic worker registry and checkpoint-request receipts.
- [x] Implement preflight/step watchdog and exact `SIGTERM` emergency enforcement.
- [x] Implement a JSON-only guard-check CLI and rerun focused tests.

### Task 3: Consolidation and certification

**Files:**
- Modify: `README.md`
- Create: `docs/runbooks/hrl-4.md`

- [x] Document thresholds, precedence, worker registration, and operator checks.
- [x] Run the full suite, syntax/source scans, diff checks, runtime ownership, and invariance proof.
- [x] Commit only HRL-4 files as `feat: add HRL-4 Luna resource governor`.
