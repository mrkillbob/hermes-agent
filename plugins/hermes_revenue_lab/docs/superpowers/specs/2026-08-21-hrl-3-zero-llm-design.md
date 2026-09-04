# HRL-3 Zero-LLM Execution Design

## Scope

HRL-3 makes deterministic work structurally ineligible for a model. It provides a fixed task
catalog, bounded standard-library operations, and a Hermes-compatible precheck gate. It does not
create unattended schedules; HRL-14 owns cron fleet installation and activation.

## Deterministic authority

The catalog maps URL/content change detection, hashing, timestamp comparison, exact-ID
deduplication, decimal arithmetic, read-only SQLite queries, experiment metrics, revenue totals,
loopback health checks, CPU/load inspection, RAM inspection, schedule windows, and threshold
comparison to `no_llm`. A request that assigns any catalog operation to a model-backed tier is
rejected before routing.

Operations are bounded and typed. File reads have byte limits. SQLite opens in read-only/query-only
mode and permits one `SELECT` or `WITH` statement with a row cap. Decimal math never uses binary
floating point. Health checks are loopback-only and do not follow redirects. Schedule evaluation
uses an explicit IANA timezone and weekday/time window; it does not claim holiday authority.

## Hermes gate

The precheck result renders exactly one canonical JSON line. No-change or fully handled work ends
with `{"wakeAgent":false}`. Changed evidence that requires interpretation ends with
`{"context":{...},"wakeAgent":true}`. Context is bounded metadata only—hashes, counts, categorical
reason codes, and numeric values—not document bodies or secrets.

The generic cron entrypoint loads a fixed adjacent JSON configuration under `$HERMES_HOME/prechecks`
whose basename matches the script basename. Config and mutable state paths must remain under the
Revenue Lab root. Missing, invalid, or unsafe configuration exits nonzero so Hermes reports a
broken watchdog instead of silently skipping work.

## Acceptance

Tests must cover every catalog entry, model-tier rejection, decimal and SQLite correctness,
loopback-only health behavior, bounded file/state handling, schedule/threshold evaluation, exact
Hermes last-line JSON, no-change zero-wake behavior, changed wake context, malformed config
fail-closed behavior, and no model/Ollama imports or invocations.
