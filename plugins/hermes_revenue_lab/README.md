# Hermes Revenue Lab

Hermes Revenue Lab is a standalone, local-first revenue experimentation system. It is
strictly isolated from TradingBotV18 and must yield whenever Luna needs the Mac.

The repository is built in governed patch order. HRL-0 establishes the secret-safe
environment inventory, process-level write isolation, and the dedicated Hermes Desktop
connection. It does not run models, create schedules, publish, spend, or contact customers.

## HRL-0 operator entry points

- Regenerate inventory: `PYTHONPATH=src:. .venv/bin/python scripts/collect_environment_inventory.py`
- Prove write isolation: `PYTHONPATH=src:. .venv/bin/python scripts/verify_isolation.py`
- Initialize the private runtime: `PYTHONPATH=src:. .venv/bin/python scripts/init_lab_runtime.py`
- Start the temporary backend: `scripts/hermes-revenue-lab`
- Verify status and token auth: `PYTHONPATH=src:. .venv/bin/python scripts/desktop_smoke.py`

Hermes Desktop has a non-primary remote gateway named **Hermes Revenue Lab** at
`http://127.0.0.1:9120`. HRL-0 deliberately leaves that gateway offline after its smoke test;
unattended startup is deferred to HRL-4. Never print or copy `.hermes/.env` into logs,
artifacts, issue text, or chat.

See [`docs/runbooks/hrl-0.md`](docs/runbooks/hrl-0.md) for acceptance evidence and the exact
start/test/stop procedure.

## HRL-1 status

HRL-1 is acceptance-valid. Governed benchmark `20260821T075107Z-80529d75e16a` completed five of
five valid fast-tier tasks for each candidate and selected `qwen3.5:4b`. The decision is based
first on measured resource use: its peak Ollama RSS was 14.15 GB, compared with 41.90 GB for
`qwen3:4b-instruct`. The installed coding candidate was measured and rejected for invalid task
output. All candidates were released after measurement, and missing or failed higher tiers remain
explicitly unavailable. See [`docs/runbooks/hrl-1.md`](docs/runbooks/hrl-1.md).

## HRL-2 status

HRL-2 provides the single checksum-bound routing authority at
`config/model_routing_policy.json`. Only `no_llm` and `fast` are currently available; all other
tiers fail closed. The fast tier resolves to the measured HRL-1 winner `qwen3.5:4b`, all
model-backed work yields to active Luna, and executor identity receipts prevent false actual-model
attribution. See [`docs/runbooks/hrl-2.md`](docs/runbooks/hrl-2.md).

## HRL-3 status

HRL-3 makes deterministic work structurally `no_llm`. Thirteen operation classes—including hashes,
exact deduplication, Decimal metrics/revenue, read-only SQLite, loopback health, system thresholds,
and schedules—cannot be routed to a model tier. The Hermes-compatible precheck emits exact
`wakeAgent` JSON and stays silent when nothing changed. HRL-14 now owns the installed scheduler
definitions; see [`docs/runbooks/hrl-3.md`](docs/runbooks/hrl-3.md).

## HRL-4 status

HRL-4 provides the canonical Luna-yielding `revenue_guard`. Active Luna, the protected weekday
market window, an existing Revenue worker, or missing resource evidence prevents new work. Critical
pressure can terminate only a start-token-verified, registered heavy Revenue worker after its
checkpoint and stop receipt exist; the governor has no Luna stop or restart path. The operator CLI
is check-only and emits sanitized JSON. See [`docs/runbooks/hrl-4.md`](docs/runbooks/hrl-4.md).

## HRL-5 status

HRL-5 adds a private SQLite revenue ledger with exact Decimal accounting, SQL-null unknowns,
optimistic revisions, immutable events, raw promotion evidence, and retained archive findings. The
E0–E7 ladder is sequential and evidence-bound; an AI opinion cannot promote an experiment. See
[`docs/runbooks/hrl-5.md`](docs/runbooks/hrl-5.md).

## HRL-6 status

HRL-6 standardizes 21 opportunity fields, raw evidence ownership, eight ordinal score dimensions,
and seven ranking factors. Cross-domain or missing evidence fails closed. Ranking follows the
specified upside-over-labor/capital/platform-risk emphasis but publishes only a coarse A–E tier,
not a fake decimal score. See [`docs/runbooks/hrl-6.md`](docs/runbooks/hrl-6.md).

## HRL-7 status

HRL-7 provides four bounded scouts for objective business problems, historical data opportunities,
authoritative alerts, and demand-backed digital utilities. Every verdict retains public/permitted
source evidence in a private SQLite store. Model-only problem claims and generic AI-art products are
ineligible. See [`docs/runbooks/hrl-7.md`](docs/runbooks/hrl-7.md).

## HRL-8 status

HRL-8 fixes the first experiment to independent HVAC contractors in Sacramento County and enforces
an 80–120 business cohort, 10–20 objective high-confidence findings, evidence-complete sample
audits, and explicitly hypothetical price ranges. It contains no outreach transport. Real cohort
collection remains a guarded runtime activity; test fixtures are not market evidence. See
[`docs/runbooks/hrl-8.md`](docs/runbooks/hrl-8.md).

## HRL-12 status

HRL-12 adds a checksum-receipted, fail-closed compliance registry. Global prohibitions cannot be
overridden by platform entries, API and scraping actions require explicit policy plus numeric rate
limits, and missing or unclear platform policy resolves to `BLOCK_AND_REVIEW`. The initial registry
authorizes no external platforms. See [`docs/runbooks/hrl-12.md`](docs/runbooks/hrl-12.md).

## HRL-13 status

HRL-13 makes consequential external mutations human-approved by default. Approval receipts bind the
exact action, target, parameters, approver, and expiry and require operator-held authentication;
unknown mutations fail closed. No signing key or default grant is installed. See
[`docs/runbooks/hrl-13.md`](docs/runbooks/hrl-13.md).

## HRL-14 status

HRL-14 defines a checksum-bound six-role cron fleet and installs only the two roles supported by
current routing evidence: a no-agent deterministic safety check and a fast normalization gate pinned
to `qwen3.5:4b`, `ollama-launch`, and reasoning `none`. Standard, reasoning, coding, and escalation
roles are not installed. Every tick revalidates the routing policy, stored Hermes job definition,
provider binding, Luna state, and resource governor before an agent can run. The Hermes gateway
remains stopped, so automatic firing is not currently enabled. See
[`docs/runbooks/hrl-14.md`](docs/runbooks/hrl-14.md).

## HRL-15 status

HRL-15 adds a write-once, private run-artifact boundary under `artifacts/runs/<run-id>/`. Each run
binds its purpose and duration to code, routing-policy, and compliance checksums; retains source
permission evidence and exact provider/model usage; preserves unknown cost and revenue instead of
coercing either to zero; and records the experiment decision with reason codes and a ledger
reference when revenue is known. Atomic publication and a complete checksum manifest prevent
partial or silently changed runs from verifying. See [`docs/runbooks/hrl-15.md`](docs/runbooks/hrl-15.md).

## HRL-9 status

HRL-9 selects one bounded data-product hypothesis: daily model-year recall-change intelligence for
independent used-car dealers, sourced only from NHTSA’s documented non-VIN public API. The local
pipeline collects, normalizes, source-binds every record, exactly deduplicates, retains SQLite
version history, applies deterministic urgency labels, and packages a private draft through the
HRL-15 run boundary. It does not claim a particular VIN is affected, publish alerts, or treat any
monetization mode as validated. See [`docs/runbooks/hrl-9.md`](docs/runbooks/hrl-9.md).

## HRL-10 status

HRL-10 adds the selective digital-product experiment boundary: at least 36 unique eligible niche
candidates must be retained before selection; only 3–5 high-confidence functional assets may enter
a private prototype portfolio; and no SKU expansion is eligible until every initial product has
complete funnel/economics observations, non-refunded sales, and positive net revenue. There is no
listing generator, marketplace transport, or claim that synthetic tests establish real demand. See
[`docs/runbooks/hrl-10.md`](docs/runbooks/hrl-10.md).

## HRL-11 status

HRL-11 adds the mandatory customer-deliverable QA chain: artifact-hash-bound deterministic checks,
a distinct-context model review, exact platform compliance, and authenticated exact-scope approval.
All nine required quality dimensions must pass; unknown is blocking. High-value work requires the
unscheduled escalation tier, which remains unavailable, and the current Etsy policy remains
`BLOCK_AND_REVIEW`, so no current artifact is publish-eligible. See
[`docs/runbooks/hrl-11.md`](docs/runbooks/hrl-11.md).

## HRL-16 status

HRL-16 adds a loopback-only, observability-only revenue dashboard at
`http://127.0.0.1:9131`. Strict snapshots preserve signed profit and explicit unavailable fields;
the server exposes only read-only GET endpoints and rejects mutation methods. The initial live view
truthfully shows unavailable source panels until a validated local snapshot is supplied. See
[`docs/runbooks/hrl-16.md`](docs/runbooks/hrl-16.md).

## HRL-17 status

HRL-17 adds a private, write-once forecast/outcome dataset and deterministic calibration. Forecasts
must be recorded before their experiment window; outcomes cannot be attached until that window
ends. Missing actuals remain unknown and are excluded dimension-by-dimension rather than converted
to zero. The first learning asset is the retained dataset and its reproducible error/bias summary;
there is no model training or automatic ranking mutation. See
[`docs/runbooks/hrl-17.md`](docs/runbooks/hrl-17.md).

## HRL-18 status

HRL-18 adds deterministic capital recommendations for governed experiments. An increase is
recommended only when contribution margin is positive, real-customer evidence exists, automation
is stable, compliance is green, and human burden is within its declared threshold. Unknowns fail
closed to hold; negative margin or red compliance kills. Every result remains recommendation-only:
actual spending is always false and requires a separate human approval. See
[`docs/runbooks/hrl-18.md`](docs/runbooks/hrl-18.md).

## HRL-19 status

HRL-19 accounts for local model compute and every required operating-cost category. Compute cost
retains configurable runtime, low/high wattage, electricity price, measurement basis, and price
source and is reported as an interval with a midpoint estimate—not an exact amount. Missing
platform, hosting, domain, API, marketplace, payment, refund, or advertising cost keeps the total
unknown while preserving a known lower bound. See [`docs/runbooks/hrl-19.md`](docs/runbooks/hrl-19.md).

## HRL-20 status

HRL-20 adds a private write-once routing-observation dataset keyed by exact provider, model, digest,
and task type. It retains latency, compute time, success, review score, retries, escalation, final
outcome, and optional profit. Deterministic summaries optimize useful output per wall-clock second
and expose profit per compute hour only when every profit observation is known. Recommendations are
task-specific, sample-gated, and advisory; there is no fine-tuning or automatic policy mutation.
See [`docs/runbooks/hrl-20.md`](docs/runbooks/hrl-20.md).

## HRL-21 status

HRL-21 composes source-bound readiness receipts for model routing, the Luna guard, cron, sealed
artifacts, browser control, the ledger, and opportunity scoring. A first run remains blocked until
at least 20 unique scout candidates receive complete authenticated human review. It can then
nominate exactly one candidate in each of the B2B opportunity, niche-intelligence, and digital
product lanes, but it still cannot launch, publish, spend, or contact customers. See
[`docs/runbooks/hrl-21.md`](docs/runbooks/hrl-21.md).
