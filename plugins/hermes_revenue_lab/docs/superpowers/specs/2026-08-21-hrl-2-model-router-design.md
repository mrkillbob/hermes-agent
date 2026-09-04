# HRL-2 Authoritative Local Model Router Design

## Scope

HRL-2 turns the checksum-bound HRL-1 selections into the only task-to-model policy used by Revenue
Lab. It never guesses a model name, promotes an unavailable tier, downloads a model, or changes an
HRL-1 result. The router authorizes a selected tier and records bounded execution metadata; an
injected executor owns the actual task implementation and must return a source-bound identity
receipt matching the selected model and digest.

## Policy authority

`config/model_routing_policy.json` is deterministic derived policy, not independent truth. Loading
it requires all of the following:

- all three HRL-1 payload checksums match `model_benchmark_checksums.sha256`;
- `model_selections.json` binds the current benchmark ID, inventory ID, and benchmark SHA-256;
- the policy binds the selections SHA-256;
- every policy tier exactly matches a fresh derivation from the verified selections;
- an available model-backed tier has both a model name and model digest;
- an unavailable tier remains unavailable with no model.

Any mismatch raises a policy-integrity error before routing. There is no fallback to a nearby model.

## Tier controls

The fixed tier set is `no_llm`, `fast`, `standard`, `reasoning`, `coding`, and `escalation`.
`no_llm` has no model. Fast and standard disable thinking. Reasoning defaults to `low` reasoning.
Escalation is never permitted while Luna is active. The system-wide Luna-yield rule is stronger:
when Luna is active, every model-backed route is denied; deterministic `no_llm` work may proceed.

Requesting an unavailable tier produces a recorded unavailable event and never invokes the
executor. Requesting the escalation tier requires a bounded reason code. Identifiers and reason
codes use a restricted character set so prompts, secrets, and customer content cannot enter the
routing ledger.

## Execution metadata

Each authorized execution records:

- event and task identity;
- requested and actual tier;
- actual model and digest, or `null` for `no_llm`;
- escalation reason code or `null`;
- UTC start and end timestamps and measured wall time;
- categorical task result;
- retry count;
- estimated compute cost expressed as measured local compute seconds, with monetary and electricity
  cost explicitly unavailable;
- success or failure.

Raw prompts, model responses, executor results, and exception messages are never logged. Retries are
bounded to two and reuse the same selected model; a retry cannot silently escalate tiers.
An absent or mismatched executor identity receipt is a categorical task failure, so `actual_model`
can never be inferred merely from the requested route.

## Private ledger

Runtime events append as canonical JSON lines to `.hermes/router/events.jsonl`, mode `0600`. The
writer rejects paths outside the Revenue Lab root and refuses symlink targets. Writes use one
append-only file descriptor and `fsync`. The ledger is runtime evidence and remains Git-ignored.

## Acceptance

HRL-2 is complete when tests prove checksum and derivation tampering fail closed, unavailable tiers
never call an executor, active Luna denies every model-backed tier, `no_llm` remains deterministic,
successful and failed/retried tasks emit complete bounded metadata, ledger writes cannot escape the
lab root, the canonical policy regenerates deterministically, and TradingBotV18 invariance remains
unchanged.
