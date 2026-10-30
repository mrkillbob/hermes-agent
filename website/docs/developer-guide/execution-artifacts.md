# Execution-result provenance development

The `agent.execution_artifacts` module is a **disabled, host-only scaffold**.
It has no production caller, settings/environment/CLI enabler, provider adapter,
coordinator adapter, authenticated approval issuer or concrete confinement backend. Default
construction refuses before approval or execution. It cannot enable result sharing
in an installed Hermes session. Ordinary terminal-output protection is unchanged.

## Implemented boundary

An authenticated host approval contract binds task/admission/node/profile,
session/turn/request/policy, exact structured invocation digest and recipient.
The invocation is reserved before fallible execution. A trusted executor contract
returns settled, confined, bounded captures; an unsupported platform must refuse.
The capture must match both invocation ID and all invocation/task fields.

The producer deterministically filters newly captured UTF-8 stdout/stderr and
repeats the existing secret, private-path, encoding and size scans. A secret-like
label may remain blocked even after redaction: rejection is intentional. Raw
capture bytes are neither registered nor sent. The immutable presentation includes
only result kind, exit status and filtered streams, always **untrusted data**.
It does not prove that the process was correct or its output truthful.

A distinct opaque artifact reference lives in a subclass of the existing source
registry. It is not a file `SourceGrant`, source annotation or terminal trust label.
Request/turn closure drops presentations and tombstones authority, including
captures already in flight. A release binds exact request, invocation, recipient
and presentation bytes and is consumed before sending; ambiguous receipt never
permits retry. Local per-task counters span admission changes; the authority
contract must atomically enforce durable counters/revocation across registry
instances/restarts. Model and authenticated owner-channel audiences are separate.

Ceilings: 1 MiB raw capture, 32 KiB per artifact, ten release requests and 1 MiB
export per task, expiry within 24 hours. Single-artifact release is also below the
128 KiB request ceiling. Captures exceeding a ceiling are unshareable; no truncation
or rerun creates a replacement grant. No old denied payload can be imported.

## Required before activation

The abstract adapters and injected broker contract are **not confinement evidence**. Production integration
must first prove authenticated approval/recipient resolution, exact launch identity,
filesystem/network restrictions (including descendants, symlinks/reparse points,
inherited handles and no ambient credentials), durable replay/budget accounting,
revocation linearization, and bounded cleanup. If confinement cannot be enforced,
the capability must remain unavailable. Secret filtering alone is insufficient.

Provider adapters must preserve internal sidecars through native API conversion,
keep handles off wire, place result bytes only as tool data, and run the complete
existing final-request firewall against the exact wire payload. Owner-channel
adapters need a separate exact authenticated audience grant. No such adapters are
provided here; an abstract `ArtifactSink` is not transport authorization.

Raw bytes exist transiently in Python objects; immutable bytes and traceback locals
cannot promise secure erasure. Executors/callers must drop references and avoid
logging/persisting raw captures, arguments, credentials or traceback locals.
Future crash-recovery storage requires separately reviewed sealed identity,
retention and cleanup; this registry restores no authority after restart.

Tests use only synthetic host adapters and inert sinks. They prove registry/data
contracts, not OS isolation or provider acceptance. No native/live result return
claim follows from those tests. A reviewed immutable build, explicit task/project
and command scope, resolved existing account/route, byte/request budgets, expiry,
and separate activation approval are required before real sharing. This feature
must not route around a denied task or expand execution permissions.

## Durable accounting adapter (development only)

`DurableArtifactAuthority` adds private POSIX SQLite accounting behind the same
disabled constructor gate. It stores immutable host-approved metadata/digests,
never captured result bytes or command arguments. A separately authenticated host
issuer is still required; `record_approval` is not a public/worker endpoint. A
missing live-admission verifier refuses before I/O. An approval ID alone confers
no authority. Windows refuses until its private-directory ACL boundary is qualified.

Invocation IDs and `(task, request)` are durably reserved before execution, so
changing approval/invocation IDs cannot reexecute an uncertain request. Per-task
release budgets survive admissions and process restarts. Consumption commits
before sending; the shared cross-process file fence remains held through the
single send and serializes explicit revocation. The fence is now stable per
node/profile/task, so another task can progress while a send is held. Admission
snapshots run outside SQLite transactions; transactions and registry locks end
before transport begins. Synthetic tests use real spawned
processes and temporary databases, not production approvals or provider calls.

The host admission/policy mutator must share that dispatch/revocation fence; a
boolean verifier alone cannot serialize independently changing host state. No
production mutation bridge is provided. Storage assumes a trusted owning OS user
and private stable directory; it is not tamper/rollback-resistant against that
user replacing files or restoring old databases. Network filesystems, rollback
recovery and Windows ACL qualification are not supported activation claims.


## Task-scoped broker source adapter (disabled)

`BrokerArtifactExecutor` accepts only a trusted host-injected broker implementation,
existing-service qualification and immutable execution plan. The approval binds
that complete plan digest, and the broker receipt binds the complete approval,
invocation and plan. The plan preserves the assigned node/backend and per-task
CPU, memory, process, scratch/output, deadline and concurrency limits. Snapshot
and image identifiers must use `sha256:<64 lowercase hex>` and refer to previously
qualified immutable inputs. Digest syntax alone is not qualification. Only
task scratch/output are writable; the contract grants no ambient environment,
host home, daemon socket or network override. No concrete Docker client, launcher,
image pull, service start, new VM, capacity pool or fallback is supplied.

Direct stdout/stderr callbacks share the task output quota and 1 MiB raw ceiling.
Any stream error permanently poisons capture even if a broker swallows it. Success
requires the exact receipt and terminal descendant/handle settlement plus removal
of ephemeral execution state. Capture failure requests verified abort/settlement;
unknown settlement invokes durable per-task quarantine. A quarantined task cannot
be admitted, released or acknowledged revoked/reassigned, including after restart.
There is no automatic reconciliation, quarantine expiry or retry. An uncertain
executor latches refusal before invoking host quarantine; callback failure remains
a content-free denial and never restores that executor. The injected host callback
must persist reliably before runtime qualification; storage failure requires host
admission to remain stopped across recovery rather than treating restart as proof.
Control cancellation arriving during run, abort or quarantine is propagated only
after cleanup, with content-free exception text.

`TaskFences.async_scope` keeps acquisition and release in one worker. Cancellation
signals that worker and joins cleanup, including cancellation during handoff or
after adoption. A canceled waiter cannot subsequently own an abandoned file lock.
The short quarantine check runs on that same dedicated lease worker before
handoff; it cannot queue behind shared-pool workers that are already waiting on F.
Cancellation arriving during a normal scope-exit join is preserved after settlement.
Existing owner mutation gates precede F; dispatch never waits for those gates.
A host bridge must take only a short event snapshot OR a short database transaction
under F, release both before transport, and require synchronous sinks to return or
raise only after no further writes are possible. A future timeout is not proof. Inherited callback contexts reject reentry before
locking; multi-task cleanup acquires fences in canonical order. Callbacks must not
create unbound threads or wait on owner-loop/async-gate work; arbitrary unbound
callback behavior cannot be qualified by these local guards.
Quarantine blocks eligibility mutations, but a separate fence-only cleanup scope
still disposes payloads and closes file-source grants. Local disposal happens
before any fence wait, including acquisition failure; successful cleanup returns
only after the in-flight fences settle. Failure never acknowledges owner revocation
or reassignment, and retains the scope identities for explicit cleanup recovery. A refusal after durable
consumption stays spent even if no bytes were sent; cleanup cannot recreate it.
No provider/owner-channel settlement adapter is supplied in this milestone.

The source seam does **not** integrate all real SessionAuthority mutation paths,
prove lock order for those paths, or qualify native confinement. Those are explicit
activation blockers. Production issuer, final-wire provider firewall/sidecars,
settled transport and private Windows ACL qualification are still absent.
The local fence is separate from fleet leases and cannot authorize cross-host
execution/release. No distributed protocol or remote ticket is implemented; such
operations remain unavailable pending separately approved verified protocol.
