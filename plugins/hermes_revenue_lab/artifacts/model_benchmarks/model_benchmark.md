# Hermes Revenue Lab Model Benchmark

- Benchmark ID: `20260821T075107Z-80529d75e16a`
- Inventory ID: `20260820T112051Z-b1b27c347a23`
- Corpus: `hrl.benchmark.v2`
- Status: `completed`
- Result rows: `11`

## Tier selections

| Tier | Status | Model | Reason |
|---|---|---|---|
| coding | unavailable | none | benchmark evidence is incomplete or below threshold |
| escalation | unavailable | none | benchmark evidence is incomplete or below threshold |
| fast | available | qwen3.5:4b |  |
| no_llm | available | none | deterministic execution |
| reasoning | unavailable | none | no installed candidate matches the required class |
| standard | unavailable | none | no installed candidate matches the required class |

## Result summary

| Model | Role | Task | Status | Success | Guard |
|---|---|---|---|---|---|
| qwen3-coder:30b | coding | repair-collector-v1 | completed | False | FULL |
| qwen3.5:4b | fast | classify-20-v1 | completed | True | LIMITED |
| qwen3.5:4b | fast | extract-10-v1 | completed | True | FULL |
| qwen3.5:4b | fast | deduplicate-100-v1 | completed | True | FULL |
| qwen3.5:4b | fast | select-tool-v1 | completed | True | FULL |
| qwen3.5:4b | fast | decide-escalation-v1 | completed | True | FULL |
| qwen3:4b-instruct | fast | classify-20-v1 | completed | True | FULL |
| qwen3:4b-instruct | fast | extract-10-v1 | completed | True | LIMITED |
| qwen3:4b-instruct | fast | deduplicate-100-v1 | completed | True | LIMITED |
| qwen3:4b-instruct | fast | select-tool-v1 | completed | True | LIMITED |
| qwen3:4b-instruct | fast | decide-escalation-v1 | completed | True | LIMITED |

Canonical result detail and unavailable measurements are in `model_benchmark.json`.
