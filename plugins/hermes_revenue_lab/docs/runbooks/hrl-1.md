# HRL-1 Local Model Benchmark Runbook

## Current classification

HRL-1 is **acceptance-valid**.

Governed benchmark `20260821T075107Z-80529d75e16a`, bound to inventory
`20260820T112051Z-b1b27c347a23` and corpus `hrl.benchmark.v2`, completed all eleven requested model
calls. Both fast candidates produced valid structured results on all five tasks. The coding
candidate completed its one measured call but failed correctness and structured-validity gates, so
it was not selected. All three candidates were explicitly released after measurement; no Ollama
inference model remained loaded after the run.

The selected fast model is `qwen3.5:4b`. Selection prioritizes measured peak RSS, then median wall
time, then parameter count. Its measured peak Ollama RSS was 14,154,334,208 bytes and its median
wall time was 7.173149 seconds. `qwen3:4b-instruct` reached 41,898,786,816 bytes of measured peak
RSS and reduced system free memory to 23%. It was therefore
not selected on this shared Luna machine.

`qwen3-coder:30b` reached 44,194,594,816 bytes peak RSS, 20% minimum free memory, and no swap. Its
collector answer failed the fixed validator, so the coding tier remains unavailable rather than
promoting a model merely because the transport call completed.

Consequently:

- `no_llm` and `fast` are available;
- `standard` and `reasoning` remain unavailable because no installed candidate matches those
  classes;
- `coding` remains unavailable because its installed candidate failed the fixed benchmark task;
- `escalation` remains unavailable because the specified model is absent and no standard-tier
  quality baseline exists for the required material-improvement comparison;
- HRL-2 may consume only the checksum-bound `fast` and `no_llm` selections.

Earlier blocked and emergency-stop attempts remain diagnostic evidence. They established that a
foreign loaded model prevents new inference and that the suite releases only its own candidate
before moving to the next one.

## Installed candidate classification

The HRL-0 inventory, not model-name guessing, supplies candidates.

- Fast candidates: `qwen3.5:4b`, `qwen3:4b-instruct`.
- Standard 8–12B candidate: unavailable.
- Approved `gpt-oss:20b` reasoning candidate: unavailable.
- Coding candidate: `qwen3-coder:30b`; requires explicit `--role coding` and `FULL` guard state.
- Escalation candidate: `qwen3.6:27b`; requires explicit `--role escalation`, `FULL` guard state,
  and later material-quality evidence over a valid standard tier.
- `qwen3-coder-next:q4_K_M`, `glm-4.7-flash:latest`, and `qwen3.6:35b-a3b` have no automatic role.

No model was downloaded, deleted, or substituted to fill a missing tier. The final run loaded each
approved fast candidate serially and released it before considering the next candidate.

## Default safe run

From `/Users/mikedemott/HermesRevenueLab`:

```bash
PYTHONPATH=src:. .venv/bin/python scripts/run_model_benchmarks.py
```

The default requests only `fast` and `standard`. Before every task it refreshes Luna process,
Revenue Lab worker, loaded foreign Ollama model, load, free-memory, and swap evidence. There is no
protected-time or foreign-model override.

Expected exit codes:

- `0`: requested benchmarks completed;
- `2`: unavailable or partially executed;
- `3`: safely blocked by the guard.

## Heavy roles

Heavy roles are never part of the default invocation:

```bash
PYTHONPATH=src:. .venv/bin/python scripts/run_model_benchmarks.py --role coding
PYTHONPATH=src:. .venv/bin/python scripts/run_model_benchmarks.py --role escalation
```

Run only one heavy role at a time, outside the protected window, after confirming Luna is inactive.
The guard must report `FULL`; `LIMITED` denies 20B+ candidates. Do not add a bypass flag.

## Thinking control

Fast and standard requests send both:

- Ollama top-level `think: false`;
- `options.chat_template_kwargs.enable_thinking: false`.

A non-empty thinking channel is a failed result. OpenAI-style `reasoning_effort` alone is not
accepted as evidence that Qwen thinking was disabled.

## Artifact verification

Canonical outputs are:

- `artifacts/model_benchmarks/model_benchmark.json`
- `artifacts/model_benchmarks/model_benchmark.md`
- `artifacts/model_benchmarks/model_selections.json`
- `artifacts/model_benchmarks/model_benchmark_checksums.sha256`

Raw response text is not canonicalized. Each completed row stores a response SHA-256, deterministic
scores, timing/token metrics, guard evidence, and explicit unavailable resource fields. The
selection artifact binds the benchmark ID and benchmark SHA-256.

HRL-1 completion requires a fresh full test run, checksum verification, completed fast evidence,
and another TradingBotV18 invariance check. Missing standard/reasoning tiers may remain truthfully
unavailable; they must not be replaced by a nearby larger model without a separately approved and
measured candidate policy.

The user's default Hermes gateway was stopped with explicit approval before certification because
it was automatically loading a foreign fallback model. Its configuration was preserved. Restore
normal Hermes messaging after Revenue Lab work with:

```bash
/Users/mikedemott/.hermes/hermes-agent/venv/bin/hermes gateway start
```
