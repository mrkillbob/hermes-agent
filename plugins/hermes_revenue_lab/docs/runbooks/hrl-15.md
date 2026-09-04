# HRL-15 Artifact and Provenance Structure

## Outcome

Every governed experiment runner now has one required evidence boundary:
`ArtifactRunStore.write_run`. It publishes a complete run atomically to:

```text
artifacts/
  runs/
    <run-id>/
      manifest.json
      inputs.json
      sources.json
      model_usage.json
      outputs/
      logs/
      verdict.json
      checksums.sha256
```

The run directory and child directories use mode `0700`; files use `0600`. A run ID is write-once.
Existing evidence is never updated in place. `artifacts/runs/` is gitignored so local run inputs,
outputs, and logs cannot be swept into a normal source commit.

## What the evidence answers

| Question | Authoritative field or file |
|---|---|
| What ran? | `manifest.json`: experiment, task, code commit |
| Why? | `manifest.json`: bounded `run_reason` |
| Which model? | `model_usage.json`: requested/actual tier, provider, exact model and digest |
| Why did the tier change? | `model_usage.json`: required `escalation_reason` when tiers differ |
| Which sources? | `sources.json`: locator, content hash, permission basis, license/terms/robots status |
| How long? | Timestamp-derived duration in the manifest and every model invocation |
| What did it cost? | Per-invocation and total USD cost with `known`/`unknown` status |
| What did it produce? | Checksum-bound files in `outputs/` plus `output_summary` |
| Did it generate money? | `verdict.json`: known/unknown gross revenue and required ledger reference when known |
| Why promoted or killed? | `experiment_decision` and deterministic `reason_codes` |

`unknown` is not zero. If any model invocation has unknown cost, the total cost must remain unknown.
Known revenue requires a `ledger:<experiment-id>` reference. Promotion, continuation, and ordinary
completion cannot use a source whose license, terms, or robots status is unknown or prohibited;
such evidence can only be retained in a blocked run.

## Integrity and safety

- The artifact root must remain inside an explicitly supplied allowed root.
- Existing and broken symlink path components are rejected.
- Output and log names are single safe relative names; traversal and absolute paths are rejected.
- Credential-shaped keys and labeled secret text are rejected before a run directory exists.
- Publication happens in a private staging directory and becomes visible through one same-filesystem
  rename only after every file and checksum is complete.
- `checksums.sha256` covers every payload file, including outputs and logs. Missing, changed,
  symlinked, or untracked files fail independent verification.
- All JSON envelopes bind to the same run ID and are reconstructed through strict schemas during
  verification.

The Obsidian vault remains narrative-only. It cannot replace these artifacts, the revenue ledger,
or fresh verifier output.

## Verification

Programmatic:

```python
result = verify_run(run_directory, allowed_root=repository_root)
assert result.valid, result.reasons
```

Operator CLI:

```bash
scripts/verify_run_artifact.py \
  artifacts/runs/<run-id> \
  --allowed-root /Users/mikedemott/HermesRevenueLab
```

Test evidence:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_provenance_artifacts.py
PYTHONPATH=src python3 -m pytest -q
```

The tests cover exact model identity, no-LLM receipts, explicit unknowns, source permission gates,
atomic private publication, immutable run IDs, root and symlink containment, credential rejection,
checksum tampering, untracked artifacts, and independent schema verification. Synthetic fixtures
are structural evidence only; they do not establish business revenue or market acceptance.
