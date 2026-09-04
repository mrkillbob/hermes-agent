# Hermes Revenue Lab — Claude Project Context

Hermes Revenue Lab is now an in-tree hermes-agent plugin at `plugins/hermes_revenue_lab/` (folded
in from the former standalone `mrkillbob/HermesRevenueLab` repo). Work within that directory
unless the user explicitly scopes another path. Read and follow `AGENTS.md` (in this same
directory) before changing anything; its safety, verification, model-routing, and vault-authority
rules apply in full.

Key boundaries:

- Luna owns machine priority. Revenue Lab model work pauses when Luna or resource evidence says so.
- Only `no_llm` and the benchmark-selected `fast` route (`qwen3.5:4b`) are currently available.
- Do not invent fallbacks, download models, or promote unavailable tiers.
- Consequential external actions require deterministic compliance checks and authenticated approval.
- Preserve dirty work and stage only files belonging to the current patch.
- `/Users/mikedemott/HermesRevenueLabVault` is a separate narrative-only Obsidian vault, not runtime
  authority.

Run focused tests first and the full suite before claiming completion. Never call a partial,
sandbox-blocked, or unexercised result acceptance-valid.
