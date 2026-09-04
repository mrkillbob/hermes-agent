# HRL-3 Zero-LLM Execution Runbook

## Current classification

HRL-3 is implemented but creates no unattended schedule. It supplies the deterministic execution
and Hermes precheck boundary that later collectors and HRL-14 cron jobs must use.

The following operation identifiers are structurally bound to `no_llm`:

- `url_change`, `document_hash`, and `timestamp_compare`;
- `exact_id_deduplicate`, `decimal_arithmetic`, and `sqlite_query`;
- `experiment_metrics` and `revenue_calculation`;
- `health_endpoint`, `cpu_load`, and `ram_inspection`;
- `market_schedule` and `threshold_compare`.

Passing one of these identifiers to `ModelRouter.resolve(..., operation=...)` or
`ModelRouter.execute(..., operation=...)` with any model-backed tier raises before executor
invocation.

## Deterministic operation boundary

`src/hermes_revenue_lab/deterministic/operations.py` contains bounded standard-library owners:

- file hashing is size-limited and root-contained;
- URL change is evaluated from a collector's bounded local snapshot digest—network collection is
  owned by later source patches, not an LLM;
- timestamps are timezone-aware ISO-8601 values;
- IDs are exact and first-row preserving;
- arithmetic, revenue, thresholds, and experiment ratios use `Decimal`;
- SQLite is `mode=ro`, `query_only`, one `SELECT`/`WITH` statement, and row-capped;
- health checks use direct loopback HTTP only and do not follow redirects;
- schedule checks require an explicit IANA timezone and do not claim holiday authority.

## Hermes precheck contract

The installed Hermes scheduler reads the last non-empty stdout line. Revenue Lab renders exactly:

```json
{"wakeAgent":false}
```

when no interpretation is needed. A changed bounded snapshot renders a true gate with metadata-only
context, for example a SHA-256 and `content_changed` reason code. Raw content never enters the gate.

Run a configured precheck manually:

```bash
PYTHONPATH=src:. .venv/bin/python scripts/zero_llm_precheck.py \
  --config /Users/mikedemott/HermesRevenueLab/.hermes/prechecks/example.json \
  --allowed-root /Users/mikedemott/HermesRevenueLab
```

For Hermes cron, copy the entrypoint under `$HERMES_HOME/scripts/<job-name>.py` and place its config
at `$HERMES_HOME/prechecks/<job-name>.json`. With no arguments, the script resolves that matching
basename. HRL-14 will own those copies, job definitions, schedules, and activation.

## Failure behavior

Missing or malformed configuration, unknown operations, unsafe paths, symlink state, oversized
inputs, invalid prior state, and secret-labeled values exit nonzero. Hermes therefore reports a
broken watchdog instead of quietly suppressing the agent. State files are atomically replaced with
mode `0600` and contain only a checksum.
