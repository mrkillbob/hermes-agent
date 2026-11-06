# Advanced Delegation 1.1.0 Routes, Budgets, and Escalation

Routing principles
- Prefer tested local models for coding/analysis when viable.
- Route small cloud tasks to free/grant tiers after verifying allowance windows.
- Escalate only on evidence (task class + size + failure), never by generic "fast/deep".

Task classes → routes (examples)
- analysis → openrouter: thinkingmachines/inkling-small:free (reasoning: medium); fallback: local llamacpp Devstral-Small-2-24B Q4_K_M
- code → local llamacpp Devstral-Small-2-24B Q4_K_M (reasoning: none); fallback: profile: architecture-steward
- retrieval/web → profile: archivist (uses its profile chain)

Budgets/allowances
- Verified grants (sources):
  - OpenRouter free Inkling Small allowances and reset policy — see provider docs.
  - Desktop quota/model-usage-status plugins (plugin-catalog/quota.yaml, model-usage-status.yaml) expose read-only windows.
- Enforcement: provider checks an on-disk usage cache path or explicit allowances in config; refuses when exhausted, then tries local/profile fallback.

Exhaustion/failure/fallback
- Cloud 429/insufficient allowance → fallback to local/profile.
- Unknown task class → inherit default delegation route.

CI/benchmarks
- Representative micro-bench emits latency/usage JSON to artifacts (evals/advanced_routing/bench.py).

Escalation policy
- Step-up only with evidence: large context, prior fallback failure, or explicit task class mapping.
- Never create new paid commitments or read credentials; use existing profiles’ providers and keys.
