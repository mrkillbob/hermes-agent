# Accepted artifacts with restricted execution

A completed read-only report may satisfy its original deliverable while an earlier
worker attempt remains blocked by privacy policy. `kanban_artifact_acceptance`
provides an operator-only composition API for that distinction. It does not expose
a mutation command, worker tool, or HTTP endpoint.

`record_restricted_artifact_acceptance` requires a canonical existing board
connection, a complete reviewed card revision, exact artifact and provenance
bytes, original title/body and accepted-comment hashes, and complete reviewed hold
bindings. The operator must establish that the **original** deliverable is fully
satisfied; hashes do not establish correctness or grant permission. The caller
must use normal admission coordination and respect any refusal. It refuses active
ownership, unfinished runs, dependencies, subscriptions, other holds, external
contracts, changed evidence, triggers and duplicate receipts for the same context.

Success appends one digest-only `artifact_acceptance` event. The task remains
Blocked; failure fields, runs, privacy events and workspace are untouched. It does
not unblock, dispatch, satisfy dependencies, archive, clean up, or claim that the
denied request succeeded. Existing archival refusal remains in effect. A receipt
neither prevents an independently authorized future operator action nor guarantees
indefinite retention against explicit deletion or status-independent log cleanup.

The supported explicit-path reader can project these receipts without starting
Hermes or initializing/migrating a database:

```sh
python -m hermes_cli.kanban_inspect --database /path/to/kanban.db --board default \
  --deliverables all t_01234567 t_89abcdef
```

Use `accepted` or `unaccepted` instead of `all` to filter those explicit IDs. Counts
are **only for the selected IDs**, before filtering, never whole-board counts.
Every returned card retains its raw rows/status and adds `artifact_acceptance`.
The label is “Artifact accepted — execution restricted.” This is a read-only
inspection view, not a Desktop board integration or a terminal task transition.
Normal `--census`, operational listings and dependency/dispatch rules are unchanged.

The projection reports recorded acceptance, not a new read of artifact files. Any
change to the task, original criterion, accepted comment, run/hold history or links
invalidates it; receipt events alone do not invalidate themselves. Malformed
receipts fail closed. A new review is required for changed context. No receipt
implies upstream milestones, installation, runtime success or privacy clearance.
