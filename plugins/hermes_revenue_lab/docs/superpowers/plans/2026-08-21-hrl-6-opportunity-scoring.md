# HRL-6 Opportunity Scoring Engine Plan

**Goal:** Build an evidence-complete opportunity schema and coarse deterministic ranking engine.

**Architecture:** Immutable candidates own raw evidence and one observation for every required
field. Assessments own evidence-bound ordinal dimensions and ranking factors.

### Task 1: Standard evidence schema

- [x] Write failing completeness, unavailable, and evidence-reference tests.
- [x] Implement exact field codebooks and immutable candidate types.

### Task 2: Ordinal assessment and ranking

- [x] Write failing dimension/factor validation and ordering tests.
- [x] Implement evidence-domain checks, A-E tiers, and exact fraction sorting.

### Task 3: Certification

- [x] Document score direction and ranking semantics.
- [x] Run focused/full tests, syntax/diff checks, live guard, and invariance proof.
- [x] Commit only HRL-6 files.
