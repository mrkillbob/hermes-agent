# HRL-2 Authoritative Model Router Runbook

## Current classification

HRL-2 is implemented on top of benchmark `20260821T075107Z-80529d75e16a`. The canonical policy is
`config/model_routing_policy.json` and is deterministic: regenerating it from unchanged HRL-1
evidence produces SHA-256 `5b4052755d0cf7c8c2c40888a781566ff3616b323bddca3a824ad99a55c9464a`.

Current tier availability:

| Tier | Status | Model | Control |
|---|---|---|---|
| `no_llm` | available | none | deterministic; permitted while Luna is active |
| `fast` | available | `qwen3.5:4b` | thinking disabled; denied while Luna is active |
| `standard` | unavailable | none | no selected installed candidate |
| `reasoning` | unavailable | none | no selected installed candidate |
| `coding` | unavailable | none | installed candidate failed HRL-1 quality gates |
| `escalation` | unavailable | none | missing candidate and quality baseline |

There is no automatic fallback. A request for any unavailable tier records `unavailable`, invokes no
executor, and raises `TierUnavailableError`.

## Regenerate and verify policy

From `/Users/mikedemott/HermesRevenueLab`:

```bash
PYTHONPATH=src:. .venv/bin/python scripts/build_model_routing_policy.py
PYTHONPATH=src:. .venv/bin/python -m unittest tests.test_router_policy tests.test_router_policy_cli -v
```

The generator first verifies all HRL-1 payload checksums and benchmark/inventory bindings. Invalid
evidence cannot overwrite the last policy. At load time, the policy is freshly derived again and an
exact document mismatch is rejected.

## Runtime use

Load policy with `load_verified_policy(...)`, then construct `ModelRouter`. An executor receives a
`RouteDecision` and must return `TaskExecutionReceipt(value, actual_model, model_digest)`. The name
and digest must exactly match the decision. The result value is returned to the caller but never
enters routing evidence.

Pass `luna_active=True` from the governed resource observer whenever Luna owns the machine. Every
model-backed tier is then denied before executor invocation. Deterministic `no_llm` work remains
eligible because it does not load a model.

## Event ledger

Use `append_routing_event` as the router's event sink. Its default runtime destination is:

```text
/Users/mikedemott/HermesRevenueLab/.hermes/router/events.jsonl
```

The file is private mode `0600`, append-only JSONL, secret-checked, and restricted to the Revenue
Lab root. Events contain requested tier, proven actual model identity, bounded escalation reason,
timestamps, measured wall time, categorical result, retries, local compute seconds, and
success/failure. Raw prompts, results, and exception messages are excluded.

## Safety notes

- Retry count is restricted to zero through two and never changes the selected model.
- Escalation requests require a short reason code even while the tier is unavailable.
- Monetary and electricity cost remain `null`; local wall time is the only compute-cost estimate.
- Keep the user's default Hermes gateway stopped during current Revenue Lab implementation unless
  the user asks to restore it. Its configuration remains intact.
