# HRL-5 Revenue Ledger Implementation Plan

**Goal:** Add exact local experiment accounting, derived economics, evidence-bound promotion, and
archival kill findings.

**Architecture:** Immutable typed records are validated at the boundary and stored in a private,
root-contained SQLite database. Deterministic Decimal functions compute economics and promotion.

**Tech Stack:** Python 3.11 standard library, SQLite, Decimal, JSON, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-21-hrl-5-revenue-ledger-design.md`

### Task 1: Types and metrics

- [x] Write failing nullable accounting and exact metric tests.
- [x] Implement immutable experiment/evidence types and deterministic metrics.
- [x] Prove unknown propagation and undefined denominator behavior.

### Task 2: SQLite authority

- [x] Write failing containment, persistence, revision, audit, and archive tests.
- [x] Implement private root-contained schema and transactional APIs.
- [x] Preserve exact decimals and SQL nulls across round trips.

### Task 3: Promotion and certification

- [x] Write failing evidence-bound E0-E7 ladder tests.
- [x] Implement sequential promotion with no AI-opinion evidence path.
- [x] Document operator use, run full verification, and commit only HRL-5.
