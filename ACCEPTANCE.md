# Advanced Delegation 1.1.0 — Acceptance Inventory

This PR introduces an Advanced Delegation backend integrated with Hermes (>=0.22.5).

- Profiles/benchmarks inventory: see docs/advanced-delegation/ROUTES.md and docs/operations/hermes-profile-routing-20260912.json (existing). Bench harness: evals/advanced_routing/bench.py (CI runs).
- Verified grants/allowances (no secrets): authoritative sources referenced — OpenRouter free tiers; Inkling Small free; desktop quota/model-usage-status plugins. No keys logged; checks read local cache only.
- Routing: plugins/delegation/advanced-routing (task_class → profile/local/cloud, explicit reasoning, tested locals first). No generic fast/deep labels.
- Budget guardrails: provider refuses exhausted routes and falls back to configured local/profile where eligible.
- Failure/exhaustion/fallback tests: tests/tools/test_delegation_advanced_routing.py.
- Benchmarks (quality/latency/usage): evals/advanced_routing/bench.py emits JSON artifacts; wired to CI.
- Escalation policy: docs/advanced-delegation/ROUTES.md describes evidence-based escalation, never creating new paid commitments.
