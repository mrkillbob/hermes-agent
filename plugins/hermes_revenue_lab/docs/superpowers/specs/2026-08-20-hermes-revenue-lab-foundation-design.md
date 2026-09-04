# Hermes Revenue Lab Foundation Design

**Date:** 2026-08-20

**Status:** Approved for implementation

**Scope:** Master architecture boundaries and the first independently testable subproject, HRL-0.

## Purpose

Hermes Revenue Lab is a local-first system for discovering, testing, measuring, and operating legitimate online revenue experiments. It optimizes for truthful contribution margin, low inference cost, low human burden, platform compliance, and reusable datasets or products.

TradingBotV18 and Luna always have priority. Revenue Lab must stop or remain unavailable when its work could compete with Luna for resources or authority.

## Workspace and Authority Boundary

The exact workspace is:

`/Users/mikedemott/HermesRevenueLab`

The exact isolated Hermes home is:

`/Users/mikedemott/HermesRevenueLab/.hermes`

Revenue Lab must not be implemented under `/Users/mikedemott/TradingBotV18`, any TradingBotV18 worktree, or the existing `~/.hermes` state tree.

Revenue Lab has no trading authority. It must not possess brokerage credentials, import TradingBot runtime modules, call broker APIs, change TradingBot configuration, or write into TradingBotV18. TradingBot health may be observed through bounded read-only probes solely to decide whether Revenue Lab must yield.

## Operator Surface

Hermes Desktop is the primary operator surface.

Revenue Lab runs a separate loopback-only `hermes serve` backend with the custom Hermes home. Hermes Desktop registers it as the gateway `Hermes Revenue Lab`. Selecting that gateway exposes only Revenue Lab profiles, chats, sessions, memory, cron jobs, artifacts, logs, and stop state.

The existing app-managed Hermes/Luna backend remains separate and unchanged. Revenue Lab jobs are never inserted into its cron database.

The Revenue Lab backend binds only to `127.0.0.1:9120`. Implementation preflight fails closed if port 9120 is already occupied; it does not silently choose another port. The backend is not exposed to the LAN or internet.

## Isolation Layers

Isolation is defense in depth:

1. `HERMES_HOME=/Users/mikedemott/HermesRevenueLab/.hermes` scopes Hermes configuration, sessions, cron, memory, skills, logs, and gateway state.
2. `HERMES_WRITE_SAFE_ROOT=/Users/mikedemott/HermesRevenueLab` hard-blocks Hermes file-tool writes outside the lab.
3. A macOS `sandbox-exec` policy denies filesystem mutations outside the lab for Hermes terminal commands and child processes. It explicitly denies TradingBotV18 writes.
4. Revenue Lab browser automation uses a dedicated browser data directory under the lab and never attaches to the user's normal browser profile.
5. Revenue Lab credentials live only in its custom Hermes home or macOS Keychain entries dedicated to Revenue Lab.
6. Resource limits and the independent governor are added before unattended or heavy work in HRL-4.

The file-tool safe root is necessary but insufficient by itself because terminal subprocesses can bypass file-tool checks. The process sandbox is therefore required before any autonomous Revenue Lab job.

## Patch-Train Decomposition

The requested system is too large to implement as one unreviewable unit. It is divided into independently testable subprojects while preserving the requested dependency order.

### Foundation

- HRL-0: isolated workspace, secret-safe environment inventory, desktop connection design, and isolation evidence.

### Compute Control

- HRL-1: fixed task corpus and measured installed-model benchmark.
- HRL-2: authoritative evidence-derived task-to-model router.
- HRL-3: zero-LLM script execution and `wakeAgent` gates.
- HRL-4: Luna/resource governor, watchdog, checkpoint, pause, and emergency stop.

### Economic Governance

- HRL-5: SQLite revenue and experiment ledger with unknown-preserving arithmetic.
- HRL-6: evidence-backed opportunity schema and scoring.
- HRL-12: machine-readable compliance registry and fail-closed policy verdicts.
- HRL-13: human approval boundary.
- HRL-15: run manifests and provenance.
- HRL-19: configurable cost accounting and uncertainty.

### Discovery

- HRL-7: bounded business-problem, data, alert, and digital-product scouts.

### Bounded Experiments

- HRL-8: one-vertical B2B opportunity-intelligence experiment.
- HRL-9: one recurring niche-intelligence/data experiment.
- HRL-10: three to five high-confidence functional digital products.
- HRL-11: deterministic validation, independent model review, and publish eligibility.

### Operations and Learning

- HRL-14: guarded cron fleet with explicit model pins.
- HRL-16: observability/control-only local revenue dashboard.
- HRL-17: prediction-versus-outcome calibration.
- HRL-18: capital recommendations without autonomous spending.
- HRL-20: empirical model-routing improvement.
- HRL-21: dry run, scout run, human review, three bounded experiments, and truthful result measurement.

Each subproject must pass its own tests and acceptance boundary before a dependent subproject starts. Infrastructure validity never implies business validity.

The authoritative cross-group execution order remains exactly:

`HRL-0 → HRL-1 → HRL-2 → HRL-3 → HRL-4 → HRL-5 → HRL-6 → HRL-7 → HRL-12 → HRL-13 → HRL-14 → HRL-15 → HRL-8 → HRL-9 → HRL-10 → HRL-11 → HRL-16 → HRL-17 → HRL-18 → HRL-19 → HRL-20 → HRL-21`

## HRL-0 Responsibilities

HRL-0 makes no model calls, installs no models or dependencies, creates no scheduled jobs, publishes nothing, spends nothing, and contacts no customer.

It creates a deterministic inventory collector and the minimum repository structure needed to run and test it. All writes remain under the Revenue Lab workspace.

After inventory and isolation validation succeed, HRL-0 may temporarily start the isolated loopback backend on `127.0.0.1:9120`, register or verify the `Hermes Revenue Lab` Desktop connection, exercise one no-model Desktop health/session smoke path, and stop the backend. Persistent or unattended backend startup remains disabled until HRL-4 supplies the governor.

## HRL-0 Outputs

The collector writes:

- `artifacts/bootstrap/environment_inventory.json`
- `artifacts/bootstrap/environment_inventory.md`
- `artifacts/bootstrap/command_manifest.json`
- `artifacts/bootstrap/inventory_checksums.sha256`
- `artifacts/bootstrap/desktop_connection_verdict.json`

Generated artifacts are immutable per inventory run. A repeated collection creates a new run-specific source directory and updates the canonical bootstrap projections only after all validation succeeds.

## Inventory Schema

The JSON document contains these top-level sections:

- `schema_version`
- `inventory_id`
- `collected_at`
- `classification`
- `workspace`
- `hermes`
- `ollama`
- `machine`
- `storage`
- `resource_observations`
- `luna_observation`
- `schedulers`
- `browser_automation`
- `isolation`
- `unknowns`
- `warnings`
- `source_commands`

Every observation carries a status of `available`, `unavailable`, `blocked`, or `not_observed`. Unknown numeric values are JSON `null`; they are never silently converted to zero.

## Inventory Collection Rules

### Hermes

Collect version, upstream revision, installation method, Python version, enabled tool names, profile names and configured model names, cron job identifiers/names/schedules/statuses/workdirs, gateway state, MCP server names/statuses, computer-use version/status, and project-list availability.

Do not collect prompt bodies, conversation contents, environment-variable values, authentication state contents, cookies, or tokens.

### Ollama

Collect version, installed model name, digest, disk size, parameter count, architecture, quantization, context length, advertised capabilities, and currently loaded model resource information.

Do not invoke a model during HRL-0.

### Machine and Storage

Collect Mac model, model identifier, chip, performance/efficiency core counts, total RAM, filesystem capacity, used space, and available space.

Do not store serial number, hardware UUID, provisioning identifiers, network identifiers, or account identifiers.

### Resources

Collect bounded samples of load average, CPU utilization, memory pressure, free memory, compressed memory, swap activity, and aggregate CPU/RSS for processes classified as Luna/TradingBot, Hermes, Ollama, and Revenue Lab.

Persist process category, count, aggregate CPU, aggregate RSS, and detection evidence. Do not persist complete process command lines.

### Schedulers

Collect Hermes cron metadata, user-crontab availability, and relevant launchd labels/statuses. Do not copy job prompt bodies or embedded secrets.

### Browser Automation

Collect installed browser-automation mechanisms, versions, permission status, and profile-directory metadata. Do not inspect browsing history, cookies, saved passwords, page content, or the user's default browser identity.

## Busy and Idle Classification

The first observed snapshot is classified from evidence. It is not called idle merely because TradingBot is absent.

An idle baseline requires all of the following across multiple samples:

- no active Luna or TradingBot process;
- no loaded Ollama model;
- no Revenue Lab job;
- system load and memory pressure below conservative thresholds defined in the collector configuration;
- stable samples for the required observation window.

If the conditions are not observed, `idle_baseline.status` is `unavailable`. HRL-0 reports that limitation rather than fabricating an idle footprint.

## Redaction and Secret Safety

Collection uses an allowlist, not a denylist. Raw configuration and authentication files are never copied.

The collector rejects output containing known secret-key names or credential-file payloads. It also rejects machine serials, hardware UUIDs, and full process command lines. A validation failure prevents canonical inventory publication and leaves the rejected run diagnostic-only.

Existing Hermes configuration is never overwritten or copied into Revenue Lab. Before any later mutation to Revenue Lab's own configuration, that isolated configuration is backed up within the lab with restrictive permissions.

## Isolation Evidence

HRL-0 produces evidence that:

- writes inside Revenue Lab succeed;
- a write-only, non-truncating open of an existing TradingBotV18 file is denied by the process sandbox;
- the probed TradingBot file hash is identical before and after;
- TradingBotV18 Git status is identical before and after;
- the sandbox does not kill, pause, restart, or signal any TradingBot/Luna process.

The external-path probe never writes bytes, truncates a file, creates a file, or removes a file. If the sandbox permits the write-only open, isolation fails and HRL-0 cannot complete.

## Error Handling

Every command has a bounded timeout and captures exit classification without persisting unsafe raw output.

- Missing optional tools produce `unavailable` observations.
- Permission failures produce `blocked` observations.
- Parser failures retain sanitized diagnostic metadata and block canonical publication for the affected required section.
- A partial run remains under its run-specific directory and is never promoted to the canonical inventory.
- Existing canonical inventory files are replaced atomically only after schema, redaction, checksum, and Markdown/JSON consistency checks pass.
- HRL-0 never attempts to repair Hermes, Ollama, browser permissions, cron, launchd, or Luna.

## Verification

Tests must cover:

- schema construction;
- unavailable versus zero handling;
- allowlisted command parsing;
- secret and hardware-identifier rejection;
- process-command redaction;
- installed-model parsing;
- loaded-model parsing;
- Hermes cron metadata parsing without prompt capture;
- busy/idle classification;
- atomic canonical publication;
- failed-run retention;
- Markdown/JSON consistency;
- safe-root behavior;
- process-sandbox write denial;
- unchanged TradingBot hash and Git status evidence.

The final HRL-0 verification includes unit tests, one live secret-safe inventory run, JSON schema validation, checksum verification, and the non-mutating isolation probe. Outcomes are classified as `infrastructure-valid`, `diagnostic-only`, `environment-blocked`, or `logic-regression`.

## HRL-0 Acceptance

HRL-0 is infrastructure-valid only when:

- the workspace and repository exist outside TradingBotV18;
- the required JSON and Markdown inventories exist and agree;
- installed software and models are captured without new installations;
- secret scanning passes;
- the current resource snapshot is truthfully classified;
- required unknowns or blocked observations are explicit;
- the lab sandbox allows lab writes and denies TradingBot write access;
- TradingBot hashes and Git state are unchanged;
- tests pass;
- no persistent Revenue Lab process, cron job, model inference, publishing action, spending action, or outreach occurred.

An unavailable idle baseline is reported as an explicit remaining HRL-0 acceptance gap unless a quiet sampling window is observed. It cannot be waived by relabeling a busy snapshot.

## Deferred Work

Model benchmarking, model selection, routing, cron creation, persistent or unattended Desktop backend startup, browser-profile creation, resource preemption, accounting, scouts, experiments, publishing, and dashboard construction are not HRL-0 behavior. They begin only in their dependent subprojects.
