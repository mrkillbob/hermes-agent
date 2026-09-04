# HRL-14 Fail-Closed Hermes Cron Fleet

## Current classification

HRL-14 is implemented and manually exercised. Two eligible definitions are installed in Hermes,
but the Hermes gateway remains stopped, so they cannot fire automatically. Manual direct-run
evidence proved both script gates ran and the model-backed job skipped its agent.

| Role | Tier | Schedule | Installed | Model/provider |
|---|---|---:|---:|---|
| Frequent deterministic checks | `no_llm` | every 15 minutes | yes, `9d5a1505798b` | none; `no_agent` |
| Lightweight opportunity normalization | `fast` | daily 18:30 Pacific | yes, `2db015b642e4` | `qwen3.5:4b` / `ollama-launch`, reasoning `none` |
| Daily opportunity report | `standard` | daily 18:15 Pacific | no | selected tier unavailable |
| Weekly experiment review | `reasoning` | Sunday 19:00 Pacific | no | selected tier unavailable |
| Coding/build queue | `coding` | weekdays 20:00 Pacific | no | selected tier unavailable |
| Tier-4 high-value escalation | `escalation` | no schedule | no | manual/high-value flag required and tier unavailable |

The installed job records live in Hermes' per-profile scheduler state. The source authority is
`config/cron_fleet.json`, authenticated by `config/cron_fleet.sha256` and bound to the verified
HRL-2 routing-policy checksum.

## Preflight boundary

Before any eligible tick, `scripts/cron_preflight.py` verifies:

1. the fleet manifest checksum;
2. the complete HRL-1 evidence chain and derived HRL-2 routing policy;
3. the current stored Hermes job definition, including schedule, enabled state, script, workdir,
   model, provider, reasoning effort, delivery target, and `no_agent` mode;
4. the live Hermes provider, default alias, loopback endpoint, and model inventory;
5. the deterministic HRL-4 Luna/resource decision; and
6. for normalization, the existence of bounded eligible scout work.

A failed model-job preflight emits exactly `{"wakeAgent":false}`, which Hermes evaluates before
constructing the agent. A no-agent safety check remains model-free and reports a bounded reason code
if its own preflight breaks. Unknown or duplicate installed definitions fail closed.

## Installation and maintenance

Render a no-write plan:

```bash
PYTHONPATH=src:. python3 scripts/install_cron_fleet.py
```

Install the enabled definitions only:

```bash
PYTHONPATH=src:. python3 scripts/install_cron_fleet.py --install
```

The installer refuses duplicate names because it cannot authenticate a pre-existing definition by
name alone. When the repository preflight changes without a manifest/job change, refresh the two
mode-`0700` Hermes script copies explicitly:

```bash
PYTHONPATH=src:. python3 scripts/install_cron_fleet.py --refresh-scripts
```

The four unavailable roles are represented in the signed manifest but cannot be rendered into
create commands. Tier 4 has no cron expression and cannot be blindly scheduled even after a future
model qualifies.

## Manual exercise evidence

The final refreshed scripts share SHA-256
`ac687c0bd977a00b80caee8f0f8f3b7045a6d42574abe56c3bc93780aa3d85b0`. Direct runs at approximately
02:48 Pacific on 2026-08-21 produced:

- deterministic job: `silent (wakeAgent=false)` in no-agent mode;
- normalization job: `Script gate returned wakeAgent=false — agent skipped`;
- Hermes log: explicit `wakeAgent=false, skipping agent run` for `2db015b642e4`.

Ollama already had resident models before the final direct-run check, so that before/after sample is
not proof that no other process loaded a model. The authoritative scheduler output is narrower: the
HRL normalization agent was skipped. A separate Hermes Desktop session was still completing and
retrying a 64K-context fallback request, which reloaded `hermes-qwen3-fast:latest` after its first
runner was force-stopped. Hermes Desktop was then closed through its `Quit Anyway` stop boundary,
ending the retrying chat. Both Hermes-loaded models were unloaded normally afterward. The Ollama
service stayed up, and the final `ollama ps` inventory was empty.

## Verification

```bash
PYTHONPATH=src:. PYTEST_ADDOPTS='-p no:cacheprovider' \
  python3 -m pytest -q tests/test_cron_fleet.py
PYTHONPATH=src:. python3 scripts/install_cron_fleet.py
hermes cron list --all
hermes cron status
```

The focused HRL-14 corpus currently has 15 passing tests. Automatic execution remains deliberately
disabled at the gateway boundary; do not call the installed definitions acceptance-valid for
unattended operation until the gateway is separately enabled and natural runs are observed.
