# HRL-1 Local Model Benchmark Design

## Scope

HRL-1 measures installed Ollama candidates on a fixed Revenue Lab corpus and publishes the evidence
needed by HRL-2. It does not download models, change Ollama configuration, select by parameter count,
or run heavy inference while Luna or the protected market window owns the machine.

The certified HRL-0 inventory is the candidate authority. Missing candidate classes remain
`unavailable`; a nearby large model is never silently substituted for an absent 8–12B standard or
20B reasoning model.

## Safety boundary

Every model invocation requires a fresh benchmark guard decision. The guard denies execution when:

- a governed Luna/TradingBot process is observed;
- local time is inside the conservative weekday market-protection window;
- memory, swap, or load evidence is unavailable or outside limits;
- the candidate is 20B+ and the guard is not `FULL`;
- another Revenue Lab model worker is active;
- Ollama has a loaded model other than the current serial benchmark candidate.

Denied work produces a structured `blocked` result and no Ollama request. There is no command-line
override for protected time or active Luna. Heavy benchmarks are operator-started after the guard
becomes `FULL`.

## Corpus

The fixed, local, synthetic corpus has ten task families matching the master brief:

1. classify 20 opportunities;
2. extract structured data from 10 page fixtures;
3. deduplicate 100 records;
4. analyze one business;
5. produce an opportunity score;
6. select a declared Hermes-style tool correctly without executing it;
7. synthesize several local source excerpts;
8. write or repair a small Python collector;
9. produce a structured audit;
10. decide whether escalation is warranted.

Prompts contain no credentials, customer data, network content, or TradingBot material. Expected
answers and validators are versioned with the corpus. Structured tasks use JSON schemas and are
scored deterministically; rubric-only quality dimensions remain explicitly `not_observed` until a
separate governed reviewer exists.

## Ollama protocol and thinking control

The client talks only to `http://127.0.0.1:11434/api/chat`, with streaming enabled so time to first
token can be measured. Each request pins the exact inventory model name and digest. Fast and
standard tasks send both Ollama's top-level `think: false` control and the template option
`chat_template_kwargs: {enable_thinking: false}`. The harness rejects a response containing a
thinking channel when thinking is disabled.

No OpenAI-style `reasoning_effort` field is treated as sufficient thinking control for Qwen.
Reasoning levels are mapped only for candidates whose installed metadata and a live control probe
show the capability.

## Measurements

Each result records:

- model identity, digest, quantization, task and corpus version;
- wall time, time to first token, prompt/eval token counts, and tokens per second;
- Ollama-reported load, prompt-eval, eval, and total durations;
- peak observed Ollama RSS, load average, free-memory percentage, and swap before/after;
- structured-output validity, deterministic correctness, tool-call correctness, retry count,
  success, and unnecessary-thinking verdict;
- guard state and any unavailable measurement reason.

GPU pressure that cannot be measured with an allowlisted local interface remains `null`; it is not
inferred from model size or processor labels.

## Selection

Selection is deterministic and evidence-bound:

- candidates must complete the required tier task set;
- structured validity and task success must meet the tier threshold;
- every selectable candidate must have positive measured peak-RSS evidence for every required task;
- the winner has the lowest measured peak RSS, then median wall time, then parameter count;
- Tier 4 is selected only when its quality improvement over the lower tier exceeds the configured
  materiality threshold;
- missing or incomplete evidence yields `unavailable`, never a provisional default.

The selected-tier document includes the benchmark inventory ID and result checksum so HRL-2 can
reject stale or hand-edited policy.

## Artifacts

Canonical outputs:

- `artifacts/model_benchmarks/model_benchmark.json`
- `artifacts/model_benchmarks/model_benchmark.md`
- `artifacts/model_benchmarks/model_selections.json`
- `artifacts/model_benchmarks/model_benchmark_checksums.sha256`

Per-run raw model text is retained only in Git-ignored run directories. Canonical artifacts contain
scores, metrics, hashes, and bounded diagnostics—not prompts containing external data or full model
responses.

After each serial candidate, the suite invokes an exact-name release boundary before another model
can start. A failed release makes the run partial and prevents the next candidate from launching.

## Acceptance

HRL-1 is complete only when:

- corpus and validators pass deterministic tests;
- a blocked protected-window run proves zero Ollama calls;
- thinking-disable request and response checks are tested;
- canonical artifacts validate and checksum correctly;
- each selected tier is backed by successful live measurements, or is explicitly `unavailable`;
- no large model was run during a protected state;
- TradingBotV18 invariance is re-proven.
