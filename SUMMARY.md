# MCP‑serve A2A Readiness Summary

[![mcp-serve-readiness](https://github.com/Coding-Sensations/hermes-agent/actions/workflows/mcp-serve-readiness.yml/badge.svg?branch=mrkillbob-hermes-mcp-serve-readiness)](https://github.com/Coding-Sensations/hermes-agent/actions/workflows/mcp-serve-readiness.yml?query=branch%3Amrkillbob-hermes-mcp-serve-readiness)

- Catalog pin: mcp‑serve version 1.0.0 (plugin-catalog/mcp-serve.yaml)
- Enforced read-only tools (test/a2a-test, or HERMES_MCP_READONLY=1):
  - web_search, web_extract, vision_analyze, skills_list, skill_view
- Durability tests (A2A subset): 257 tests passed locally across 13 files
  - Event stream/limits, client receipts, rejected resend, forwarded cancel retry,
    tools gate, gateway session A2A + events, hermes_state tasks, reason roundtrip
- Approvals remain disabled; ESTOP preserved
- A2A MCP resources: hermes-identity, hermes-a2a-peers, hermes-a2a-conversations (read-only)
- Notable notes: no flakiness observed locally; `event_limits` is longest (~40s)

CI artifacts
- Workflow: “mcp-serve-readiness” on PRs (retention: 14 days)
- Artifacts: hermes-mcp-serve-readiness-${{ github.run_id }}
  - artifacts/tests.log (durability run)
  - artifacts/identity.json (handshake dump)
  - artifacts/qualify.json (pin + gating summary)

Latest run + artifacts: see badge link above → select latest run for this branch, then Artifacts.
