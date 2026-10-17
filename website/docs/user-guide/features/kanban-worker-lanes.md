# Kanban worker lanes

A **worker lane** is a class of process that the kanban dispatcher can route tasks to. Each lane has an identity (the assignee string), a spawn mechanism, and a contract for what it must do with the task once spawned.

This page is the contract. It exists for two audiences:

- **Operators** picking which lanes to wire into a board (which profiles to create, which assignees to use).
- **Plugin / integration authors** wanting to add a new lane shape (a CLI worker that wraps Codex / Claude Code / OpenCode, a containerised review worker, a non-Hermes service that pulls tasks via the API).

If you're writing the worker code itself — the agent that runs *inside* a lane — the kanban lifecycle and reference details are injected into the worker's system prompt automatically (the `KANBAN_GUIDANCE` block in [`agent/prompt_builder.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/prompt_builder.py)).

## The hierarchy

```text
Hermes Kanban  =  canonical task lifecycle + audit trail
Worker lane    =  implementation executor for one assigned card
Reviewer       =  human or human-proxy that gates "done"
GitHub PR      =  upstreamable artifact (optional, for code lanes)
```

Hermes Kanban owns lifecycle truth — `ready` → `running` → `review` / `blocked` / `done` / `archived`. Worker lanes execute work but never own that truth; everything they do flows back through the kanban kernel via the `kanban_*` tools (or, for non-Hermes external workers, via the API). Reviewers gate the transition from "code change written" to "task done."

## What a lane provides

To be a kanban worker lane, an integration must provide three things:

### 1. An assignee string

The dispatcher matches `task.assignee` against either a Hermes profile name (the default lane shape) or an explicitly registered `external-` assignee (the source integration boundary — see [Adding an external CLI worker lane](#adding-an-external-cli-worker-lane) below). Tasks whose assignee doesn't resolve are left on `ready` with a `skipped_nonspawnable` event so a board operator can fix them; they are not silently dropped or executed by an arbitrary fallback.

### 2. A spawn mechanism

For Hermes profile lanes, the dispatcher's `_default_spawn` runs `hermes -p <assignee> chat -q <prompt>` (or the equivalent module form when the `hermes` shim isn't on `$PATH`) inside the task's pinned workspace, with these env vars set:

| Variable | Carries |
|---|---|
| `HERMES_KANBAN_TASK` | the task id the worker is operating on |
| `HERMES_KANBAN_DB` | absolute path to the per-board SQLite file |
| `HERMES_KANBAN_BOARD` | board slug |
| `HERMES_KANBAN_WORKSPACES_ROOT` | root of the board's workspace tree |
| `HERMES_KANBAN_WORKSPACE` | absolute path to *this* task's workspace |
| `HERMES_KANBAN_RUN_ID` | the current run's id (for the lifecycle gate) |
| `HERMES_KANBAN_CLAIM_LOCK` | the claim lock string (`<host>:<pid>:<uuid>`) |
| `HERMES_PROFILE` | the worker's own profile name (for `kanban_comment` author attribution) |
| `HERMES_TENANT` | tenant namespace, if the task has one |

For non-Hermes lanes, a trusted in-process owner can explicitly supply `ExternalLanes` through `dispatch_once(spawn_fn=...)`. A plain custom `spawn_fn` does not bypass profile validation. No plugin discovery or installed CLI runner is registered automatically.

### Descendant process scope

A task assignment belongs to the dispatcher worker, not to every program it starts.
Hermes subprocess helpers carry a non-owner fence into shells, execution kernels,
cron deliveries, hooks, language servers, and ordinary stdio MCP servers. Later
children remain fenced even when a script removes the inherited task ID: CLI and
tool mutations are rejected, rather than treating that script as an orchestrator.
Board/database routing and workspace paths are retained. Descendants can read an
existing board without running schema migrations; its owner must initialize it.
The fence is scoped to the lineage's board root (the marker's value is that root, plus the
dispatcher-pinned `HERMES_KANBAN_DB`): a descendant that works against a different Kanban
home — a test or reproduction under a scratch `HERMES_HOME` — gets a normal read-write board.

The dispatcher explicitly grants a newly assigned worker its own scope. The managed
Hermes-tools MCP endpoint can likewise act for its supervising worker, while the
executor's ordinary shell children remain fenced. Workers may only perform lifecycle
handoffs and attach files to their assigned task; `unblock` remains orchestrator-only.
Cross-task comments and follow-up task creation retain their existing behavior.

Integration authors spawning code should use
`agent.delegation_context.delegated_child_subprocess_env` at the actual spawn, after
merging environment overrides. It preserves the caller's credential/profile policy.
This is cooperative runtime scoping, **not OS confinement**: it does not prevent
arbitrary code from deliberately erasing lineage metadata or opening SQLite directly.

### 3. A lifecycle terminator

Every claim must end in exactly one of:

- `kanban_complete(summary=..., metadata=...)` — task succeeds, status flips to `done`.
- `kanban_request_review(summary=..., metadata=..., reviewer=...)` — same-card implementation is complete and enters first-class review; status flips to `review`. The dispatcher loads the bundled `sdlc-review` skill unless `kanban.review_dispatch` is disabled. A reviewer approves with `kanban_complete`, returns actionable rework with `kanban_request_changes`, or escalates a genuine external blocker with `kanban_block`.
- `kanban_block(reason=...)` — task waits for human input, status flips to `blocked`. The dispatcher respawns when `kanban_unblock` runs.
- The worker process exits without a tool call. The kernel reaps it and emits `crashed` (PID died) or `gave_up` (consecutive-failure breaker tripped) or `timed_out` (max_runtime exceeded). This is the failure path; healthy workers don't end here.

The kanban kernel enforces that exactly one of these terminates each run. A worker that calls neither and exits normally is treated as crashed.

## Outputs and the review handoff

For code-changing tasks, pick the review model encoded by the task graph:

- **Same-card review:** call `kanban_request_review(summary=..., metadata=..., reviewer=...)`. The task enters `review` without touching block recurrence accounting. The dispatcher claims it with the bundled `sdlc-review` skill by default. The reviewer approves with `kanban_complete`, calls `kanban_request_changes(reason=...)` to close the review run and route the task back to its original implementer, or blocks only for a genuine external escalation.
- **Pre-created downstream review/QA/release card:** `kanban_show` lists child IDs; inspect those cards with `kanban_show(task_id=...)` before choosing the terminal action. When a child is the downstream review/QA/release phase, call `kanban_complete` on the implementation phase. It cannot promote until this parent is `done`/`archived`. Do not additionally request same-card review and never sticky-block the parent with `review-required:` — either choice strands or duplicates the downstream lane.
- **Human-only boards:** set `kanban.review_dispatch: false`. A task can then remain in `review` until a human approves it or uses `reopen-review`/the dashboard to return it to `ready`/`todo`.

Both review models carry their structured handoff on the lifecycle transition itself. Do not place secrets, tokens, or raw PII in `summary` or `metadata`; run rows are durable.

The injected `KANBAN_GUIDANCE` covers both graph shapes, `kanban_complete`, the same-card review loop, and `kanban_block` for genuine blockers.

## Logs and audit trail

The dispatcher writes per-task worker stdout/stderr to `<board-root>/logs/<task_id>.log`. Logs are auditable from kanban metadata:

- `task_runs` rows carry the `log_path`, exit code (where available), summary, and metadata.
- `task_events` rows carry every state transition (`promoted`, `claimed`, `heartbeat`, `completed`, `blocked`, `review_requested`, `changes_requested`, `review_reopened`, `gave_up`, `crashed`, `timed_out`, `reclaimed`, `claim_extended`).
- `kanban_show` returns both, so a reviewer (or a follow-up worker) reading the task gets the full history without needing dashboard access.

The dashboard renders run history with summaries, metadata blocks, and exit-status badges. CLI users can run `hermes kanban tail <task_id>` to follow live, or `hermes kanban runs <task_id>` for the historical attempt list.

## Existing lane shapes

### Hermes profile lane (default)

The shape every kanban worker takes today: the assignee is a profile name, the dispatcher spawns `hermes -p <profile>`, the worker gets the `KANBAN_GUIDANCE` system-prompt block injected automatically, and uses the `kanban_*` tools to terminate the run. No setup beyond defining the profile.

When you create profiles for your fleet, choose names that match the *role* you want the orchestrator to route to. The orchestrator (when there is one) discovers your profile names via `hermes profile list` — there's no fixed roster the system assumes (the orchestrator side of the contract is part of the injected `KANBAN_GUIDANCE`).

### Orchestrator profile lane

A specialisation of the profile lane: an orchestrator is a Hermes profile whose toolset includes `kanban` but excludes `terminal` / `file` / `code` / `web` for implementation. Its job is decomposing a high-level goal into child tasks via `kanban_create` + `kanban_link` and stepping back. The orchestrator skill encodes the anti-temptation rules.

## Adding an external CLI worker lane

The owner-side Python boundary is `hermes_cli.kanban_external`. It is source-only:
no CLI executor, service, callback, polling loop, credentials or built-in lane
registration is supplied. A trusted owner explicitly constructs
`ExternalLanes({"external-example": Lane("executor-identity", executor)})` and
passes it to `dispatch_once(spawn_fn=lanes)`. The executor implements bounded
`submit(Delivery) -> Artifact | None` and read-only
`lookup(Delivery) -> Artifact | None`. Actual profile names retain profile dispatch,
including the existing profile allowlist. Unknown external assignees fail closed.
External review-lane dispatch is unsupported and held; use the existing profile
review path. Host capacity, dependency, claim and workspace checks still apply.

`Delivery` is immutable and binds database path, task, run, claim, lane, executor,
random idempotency key, payload digest, task title/body and resolved workspace.
Only those fields cross this boundary; no process environment, profile credentials,
callback recipient or arbitrary task metadata is inherited. A 64 KiB limit bounds
both deliveries and artifacts. `Artifact` accepts only `accepted`, `ack`, `started`,
`completed`, `question`, `blocked` or `error`, plus a summary and immutable findings
and questions. `artifact_from_envelope` validates the closed JSON-compatible
`{"artifact": ..., "sha256": ...}` shape. Digests provide correlation and integrity,
not peer authentication or task authority. An eventual transport must authenticate
its peer and enforce the owner's approved execution scope separately.

The delivery intent commits to the existing `task_events` journal **before** submit.
The task stays `running` after transport acceptance, ACK or start; a broker ACK
never means the work completed. A completed artifact calls the existing
`complete_task(expected_run_id=...)`, retaining completion and repository policies.
Question, blocked and error artifacts call `block_task` with a sticky `needs_input`
block and preserve their distinct typed evidence. Findings and questions remain
in the event artifact; no returned text executes commands or grants permissions.

Duplicate submit requests reuse the committed delivery and never call submit again.
A lost ACK, exception, dispatcher restart or crash between intent and submit is
uncertain. Automatic TTL/stale/orphan recovery holds such a run instead of guessing
from a missing local PID. An unresolved prior delivery also prevents redispatch,
even after an operator reclaim. The owner can explicitly call
`lanes.reconcile(conn, delivery)`: it applies a saved terminal artifact first,
otherwise asks **the same registered executor's read-only lookup** for a receipt.
Lookup must never start/resume work. No automatic resend, retry deadline, unsafe
"not found means retry" rule or override API is provided. Unresolved/superseded
runs require owner investigation; timeout alone cannot release the hold.

Terminal artifacts commit before lifecycle mutation, allowing reconciliation after
an interruption between the two. Identical terminal receipts are idempotent;
conflicting evidence and foreign/stale task, run, claim, executor, board or payload
bindings reject. Existing `hermes kanban tail` / `runs` surfaces retain the audit
trail in `task_events` and `task_runs`; no new queue or ledger is created.

Local CLI interfaces may be candidates for a future executor using existing approved
accounts: Codex `exec --json --output-schema` and Claude Code
`--print --output-format json --json-schema`. A process exit or parseable output is
not an authenticated ACK, an idempotent lookup guarantee, containment proof or a
native application callback. No private account tokens are read or extracted by
this boundary. A2A can be mapped to Delivery/Artifact only when existing authenticated
endpoints and task lookup capabilities are separately established. This patch
creates no A2A endpoint and no native Codex/Dot callback. The historical uncertain
App MCP callback must not be replayed; unattended result return remains unqualified.

## Explicit coordinator receipt prototype (fixture only)

`kanban_coordinator_executor.CoordinatorExecutor` is a concrete local receipt
adapter. Its `submit` records an offer in the existing Kanban event stream; it
launches nothing. `lookup` uses a read-only/query-only SQLite connection and
never starts, resumes or repairs work. Separate SQL deadlines cap application
work at 2 seconds for submit and 1 second for lookup, with lock waits capped at
200/100 ms, no retries, exact indexed queries and 64 KiB event envelopes.
Stalled kernel/filesystem operations are outside hard real-time guarantees.

An authorized owner must explicitly invoke
`kanban_coordinator_runner.receive_once` for the exact database/task/run/key and
an immutable `ExecutionPlan`. One receiver claim and one launch intent commit
before the process starts. ACK means the coordinator inbox accepted the delivery;
`started` means a process was created. Neither asserts model acceptance. There is
no watcher, automatic registration, daemon, account access or CLI activation.

This version accepts only the pinned POSIX fake program and synthetic fixture
input. Live mode, arbitrary argv and changed program/input hashes reject before
receiver admission. No Claude or Codex executable can be selected. Structured
fake provider metadata is validation test data, not proof of real model use.

The receiver drains bounded stdout/stderr under a monotonic execution deadline
(at most 120 seconds and the remaining task budget), with 64 KiB per stream.
It can terminate only its new process group while the live leader's birth identity
still matches: 3 seconds for TERM, then at most 2 seconds for KILL. A leader exit
alone is insufficient; a remaining group retains uncertainty. It never signals a
shared server or a historical session. No OS containment or escaped-descendant
protection is claimed. Receiver death or a stalled OS can leave an uncertain
attempt; these bounds require the receiver to remain alive and scheduled.
Synchronous process creation itself cannot be bounded by the subsequent execution
deadline. If process identity cannot be verified, the receiver closes its pipes
and records uncertainty without signaling the group; a child may remain alive.

Exit receipts bind launch/PID/birth, input bytes and bounded output hashes.
Result validation uses those same retained bytes, exact task/run/key/payload and
strict synthetic CLI/provider metadata before calling the existing lifecycle API.
A crash after exit or terminal persistence can reconcile from saved evidence
without another launch. Changed evidence, ambiguous start or unsettled effects
remain fenced. Evidence files are private same-user consistency records, not
cryptographic peer authentication. Existing lifecycle APIs retain their own
transaction/policy handling; their finalization latency is separate from the
child execution deadline.

Live no-tools CLI qualification, authenticated executor authority, crash-surviving
host enforcement and native application result return remain separate work. This
prototype adds no native Codex/Dot callback, SDK conversation binding or A2A endpoint.

## Failure modes the dispatcher handles

These PID-based rules describe profile workers. External delivery intents use the
conservative reconciliation hold above; their TTL or runtime expiry does not prove
that remote effects stopped.

- **Stale claim TTL** — a worker that claims and then never heartbeats / completes / blocks gets reclaimed after `DEFAULT_CLAIM_TTL_SECONDS` (15 min default) — but only if the worker process has actually died. A live worker (slow model spending 20+ min in one tool-free LLM call) gets the claim *extended* instead of killed; only a dead PID is reclaimed.
- **Crashed worker** — a worker whose host-local PID has vanished is detected by `detect_crashed_workers` and reaped; the task increments `consecutive_failures` and may auto-block when the breaker trips.
- **Run-level retry** — when a task is retried (post-block, post-crash, post-reclaim), the worker can use the `expected_run_id` parameter on terminating tools to fail fast if its own run was already superseded.
- **Per-task max runtime** — `task.max_runtime_seconds` hard-caps wall-clock time per run, regardless of PID liveness. Catches genuinely-deadlocked workers that the live-PID extension would otherwise keep running.
- **Stranded-task detection** — a ready task whose assignee never produces a claim within `kanban.stranded_threshold_seconds` (default 30 min) shows up in `hermes kanban diagnostics` as a `stranded_in_ready` warning. Severity escalates to error at 2x the threshold and critical at 6x. Catches typo'd assignees, deleted profiles, and down external worker pools in one signal — identity-agnostic, no per-board allowlist to curate.
- **Running with open parents** — a `running` card whose direct parent is not `done`/`archived` (the parent reopened mid-run, or the link predates the running-child refusal) shows a `running_with_open_parents` warning: the dependency gate is not serialising the two runs and `kanban_complete` will be refused until the parent finishes. Read-only; suggests `hermes kanban unlink`.
- **Legacy review dependency deadlock** — a parent sticky-blocked with `review-required:` while one or more direct children remain dependency-gated in `todo` produces an immediate `review_dependency_deadlock` error. The diagnostic is read-only: it suggests completing the finished phase or unlinking the incorrect edge but never removes a user block automatically.

## Related

- [Kanban overview](./kanban) — the user-facing intro.
- [Kanban tutorial](./kanban-tutorial) — walkthrough with the dashboard open.
- [`KANBAN_GUIDANCE`](https://github.com/NousResearch/hermes-agent/blob/main/agent/prompt_builder.py) — the worker + orchestrator lifecycle injected into every kanban worker's system prompt.
