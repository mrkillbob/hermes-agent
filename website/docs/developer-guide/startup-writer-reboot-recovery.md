# Prospective recovery of legacy writer records

`hermes gateway drain prepare-reboot-reconcile` can checkpoint an exact retained writer
inventory alongside its existing explicit spawn-ledger selection. This permits recovery of
legacy `unsettled` records whose process birth time was never recorded, without pretending
that an absent parent PID proves its descendants have exited.

Preparation requires ESTOP and the existing checkout, dispatch, admission, profile and ledger
custody. Each writer reference must be a securely owned regular file that can be exclusively
held. Malformed, linked, held or changing references refuse preparation. Preparation writes
only the existing prospective checkpoint; it does not delete or settle writer records, edit
the ledger, start services, release ESTOP or grant same-boot admission.

A checkpoint containing writer records uses schema 3. Reconciliation holds every retained
reference and validates the complete inventory, exact bytes, file and directory identities,
source and ledger binding, native volume identity, and a different native kernel boot on the
same host. Device numbers may change across boots; the persistent native volume identity and
other identities must still match. Changed, missing or additional records refuse recovery.
A missing, incomplete or downgraded checkpoint cannot authorize relaxed writer acquisition.
Schema 1 and 2 retain their existing ordinary writer-exclusion behavior.

The boot boundary establishes extinction of local processes from the prior kernel session.
It does not establish remote process closure or waive the remaining current guards. The
checkpoint must be created with the intended recovery source before the boot boundary;
changing that source invalidates its binding. Reboot and later service/admission actions
remain separate operations. This source feature performs none of them automatically.

If an obsolete committed schema 1/2 checkpoint already exists, pass its exact byte hash as
`--supersede-sha256` to the same preparation command. Every selected old ledger row must be
absent from the current ledger. The old checkpoint must still belong to this root and host
(and the same native volume for schema 2). The operation retains its original bytes in a
unique `.history` file, durably stages a `preparing` replacement, rechecks custody, atomically
promotes it, and only then commits it. There is no generic force/overwrite option. A collision,
held old file, changed preimage or permission failure refuses. No history or failed stage is
removed. An interruption after promotion leaves an incomplete checkpoint that refuses admission;
this command does not auto-repair or supersede incomplete/schema 3 proof.

After the verified later boot, `hermes gateway drain hold` consumes this proof through the
native startup transaction; its matching `restore --hold-id ...` uses the same guarded path.
The separate read-only `hermes gateway drain reconcile` assessment still uses ordinary writer
exclusion and can refuse these unsettled references. Assessment is not the proof consumer.

## Consuming a checkpoint whose writer files had their ctime changed

The strict proof binds every retained writer reference by its full file version, which includes
ctime. ctime is maintained by the operating system and moves on any metadata touch, so a valid
checkpoint can stop validating after its reboot even though no writer reference changed.

`gateway drain hold` and `gateway drain restore --hold-id` accept
`--attest-checkpoint-sha256 <sha256 of .startup-legacy-reboot.json>`. With the exact hash of the
committed schema-3 checkpoint, the writer references are bound by inode, size, mtime, link count and
content hash instead of ctime, and the recovery source named in the checkpoint must still exist at its
recorded path with its recorded hash. Everything else is unchanged: the same host and native volume,
the root identity, the complete ledger version, a later native boot, refusal of live writers, closure
of every selected row, and the ESTOP requirement. Added, removed, replaced or edited references still
refuse. The attested path never writes, deletes or settles a record, and hold and restore still do not
retire the retained custody records. A wrong, malformed or stale hash refuses before anything is
published.

