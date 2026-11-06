# Hermes MCP‑Serve A2A Readiness — Acceptance

This document records acceptance criteria, evidence, and security notes for the built‑in Hermes MCP server (hermes mcp serve) adapted for A2A routing and profile‑aware gating.

## 1) Inventory
- Catalog pin: mcp‑serve version 1.0.0 (plugin-catalog/mcp-serve.yaml)
- Installed/active: built‑in MCP server at agent/transports/hermes_tools_mcp_server.py
- Profile mappings and enforced read‑only tools (profiles: `test`, `a2a-test`, or `HERMES_MCP_READONLY=1`):
  - Allowed tools: `web_search`, `web_extract`, `vision_analyze`, `skills_list`, `skill_view`
  - Defense‑in‑depth: central dispatch guard in model_tools.handle_function_call blocks non‑allowlisted tools
- MCP resources (read‑only): `hermes-identity`, `hermes-a2a-peers`, `hermes-a2a-conversations`

## 2) Durability test evidence (subset per‑PR)
- CI workflow: mcp-serve-readiness
- Artifacts (retained 14 days):
  - tests.log — durability run output
  - identity.json — hermes‑identity handshake dump
  - qualify.json — pin + gating summary
- Badge to latest run (click through to Artifacts):
  https://github.com/Coding-Sensations/hermes-agent/actions/workflows/mcp-serve-readiness.yml?query=branch%3Amrkillbob-hermes-mcp-serve-readiness

## 3) End‑to‑end trace (stable IDs + digest ACK)
- Stable routing IDs are exposed via `hermes-identity` and propagated via env (HERMES_TASK_ID, HERMES_SESSION_ID, HERMES_TURN_ID, HERMES_API_REQUEST_ID). See artifacts/identity.json.
- A2A durability semantics (dedupe by eventId, cursor reset backfill, restart‑safe cursor, durable ACK) are covered by the subset tests; confirmations appear in artifacts/tests.log.

## 4) Explicit statements
- Approvals disabled; ESTOP preserved (approval tools not advertised; central guard blocks writes)
- MCP event stream is process‑local and non‑durable; Hermes remains source of truth for approvals, session lifecycle, durable completion, and digest ACK

## 5) Security / privacy review
- Scope limits: test profiles enforce read‑only tool allowlist; central dispatch re‑validates every call
- Secrets: stdout MCP wire kept clean; logs go to stderr; HERMES_REDACT_SECRETS=true by default in server entry
- Identity: profile/task/session/turn/request IDs are correlation only; no PII exfiltration
- Provider cost: no paid credentials used; only read‑only web/skills calls permitted in gated profiles

## How to retrieve artifacts
- Open the workflow page linked above, select the latest run for this branch, and download the artifact named:
  `hermes-mcp-serve-readiness-${{ github.run_id }}`
