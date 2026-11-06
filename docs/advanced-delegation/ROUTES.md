# Advanced Delegation 1.1.0 Routes, Budgets, and Escalation

Routing principles
- Prefer tested local models for coding/analysis when viable.
- Route small cloud tasks to free/grant tiers after verifying allowance windows.
- Escalate only on evidence (task class + size + failure), never by generic "fast/deep".

Routes table (examples)

| Task class  | Primary route                                           | Reasoning | Fallbacks                                      |
|-------------|----------------------------------------------------------|-----------|------------------------------------------------|
| analysis    | provider=openrouter, model=thinkingmachines/inkling-small:free | medium    | llamacpp Devstral-Small-2-24B Q4_K_M           |
| code        | provider=llamacpp, model=Devstral-Small-2-24B Q4_K_M     | none      | profile: architecture-steward                  |
| retrieval   | profile: archivist                                       | profile   | archivist profile chain                        |
| web         | profile: archivist                                       | profile   | archivist profile chain                        |

Budgets/allowances
- Verified grants (sources):
  - plugin-catalog/quota.yaml and model-usage-status.yaml expose read-only allowance windows in Desktop.
  - OpenRouter Inkling Small free tier referenced (no live calls; document terms in release notes).
- Enforcement: provider checks an on-disk usage cache path or explicit allowances in config; refuses when exhausted, then tries local/profile fallback.
- Outside-plugin guardrail: delegate summary-cap enforced by tests/tools/test_delegate_summary_budget_cap_enforced_small.py (spills full text, trims in-context).

Exhaustion/failure/fallback
- Cloud 429/insufficient allowance → fallback to local/profile.
- Unknown task class → inherit default delegation route.

CI/benchmarks
- Representative micro-bench emits latency/usage JSON to artifacts (evals/advanced_routing/bench.py). CI retains for 14 days.

Escalation policy
1) Free/grant route available and allowance > 0 → use it.
2) If exhausted or 429/timeouts on evidence → downgrade to local/profile fallback.
3) If task requires larger context or quality AND evidence (size/benchmark) supports it → step up within existing provider keys only.
4) Never create new paid commitments or store new creds; all routing uses existing profiles’ providers/keys.
