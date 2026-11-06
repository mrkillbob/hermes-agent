# Advanced Delegation 1.1.0 — Acceptance Inventory

[![Advanced Delegation CI](https://github.com/Coding-Sensations/hermes-agent/actions/workflows/advanced-delegation.yml/badge.svg)](https://github.com/Coding-Sensations/hermes-agent/actions/workflows/advanced-delegation.yml)

This PR introduces an Advanced Delegation backend integrated with Hermes (>=0.22.5).

- Profiles/benchmarks inventory: see docs/advanced-delegation/ROUTES.md (routes table + escalation) and docs/operations/hermes-profile-routing-20260912.json (existing). Bench harness: evals/advanced_routing/bench.py (CI runs; artifacts retained 14 days).
- Verified free/grant allowances (no secrets): sources — plugin-catalog/quota.yaml, plugin-catalog/model-usage-status.yaml, and internal billing lifecycle docs. OpenRouter Inkling Small free tier referenced; no API calls made.
- Routing: plugins/delegation/advanced-routing (task_class → profile/local/cloud, explicit reasoning, tested locals first). No generic fast/deep labels.
- Budget guardrails: provider refuses exhausted routes and falls back to configured local/profile where eligible. Outside-plugin guardrail confirmed by tests/tools/test_delegate_summary_budget_cap_enforced_small.py (summary cap & spill).
- Failure/exhaustion/fallback tests: tests/tools/test_delegation_advanced_routing.py.
- Benchmarks (quality/latency/usage): evals/advanced_routing/bench.py emits JSON artifacts; CI uploads artifacts/advanced_routing_bench.json (14‑day retention).
- Escalation policy: docs/advanced-delegation/ROUTES.md — evidence-based, free/grant-first, no new paid commitments.
- Grant wiring (dry-run/mock): evals/advanced_routing/mock_grant_check.py simulates eligible models/limits from non-secret sources; no creds used.
