# Hermes Revenue Lab Agent Instructions

Hermes Revenue Lab now lives in-tree inside hermes-agent, at `plugins/hermes_revenue_lab/`
(folded in from the former standalone `mrkillbob/HermesRevenueLab` repo, last synced from its
`main` at commit `47f8363870225a36cb02b06e36c4dee08f8d0dbd`). This directory is a plugin of
hermes-agent, not an independent workspace — develop it only here going forward. It is still not
part of TradingBotV18 or LunaBot; do not edit those repositories from here.

Paths below written relative to this directory (`plugins/hermes_revenue_lab/`) unless noted.

## Safety boundary

- Revenue Lab is local-first and yields to Luna.
- Default to private drafts, dry runs, simulations, and read-only collection.
- Never publish, spend, advertise, contact customers, create accounts, accept terms, enter
  contracts, issue refunds, handle sensitive customer data, or subscribe to services without the
  deterministic compliance boundary and an authenticated, unexpired approval where required.
- Never weaken the resource governor, compliance registry, approval policy, provenance checks, or
  model identity checks to make a test pass.
- Unavailable model tiers remain unavailable. Do not download or substitute a nearby model without
  a separately approved benchmark patch.
- `qwen3.5:4b` is the currently selected fast model. `standard`, `reasoning`, `coding`, and
  `escalation` remain unavailable until checksum-bound benchmark evidence says otherwise.

## Working style

- Start with `README.md`, the relevant `docs/runbooks/hrl-*.md`, and repository status.
- Preserve unrelated dirty work. Do not reset, stash, or broadly stage changes.
- Use test-driven, narrowly scoped patches and fail closed on missing evidence.
- Keep raw prompts, customer data, credentials, tokens, and `.env` contents out of commits and
  artifacts.
- Classify evidence truthfully as verified, diagnostic-only, environment-blocked, or unexercised.

## Verification

Use Python 3.11+ where available. Run from the hermes-agent repo root; focused tests may use:

```bash
PYTHONPATH=plugins/hermes_revenue_lab/src:. PYTEST_ADDOPTS='-p no:cacheprovider' python3 -m pytest -q plugins/hermes_revenue_lab/tests/<test_file>.py
```

Before completion, run the relevant focused tests, `ruff`/format checks for modified Python, and the
full suite. The isolation corpus may require permission for macOS `sandbox-exec`; report that
truthfully rather than bypassing it.

## Knowledge vault

The separate Obsidian vault is `/Users/mikedemott/HermesRevenueLabVault`. It is narrative-only and
must never authorize runtime behavior. Runtime code, checksum-bound artifacts, deterministic gates,
and fresh command output remain authoritative. Do not store secrets or sensitive customer data in
the vault.
