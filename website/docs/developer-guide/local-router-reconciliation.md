# Recovering a retained local-router record

The managed macOS router deliberately retains `server.json` when closure of its
native descendants is uncertain. A dead root PID, a loopback health response or
an old timestamp does not authorize replacement. Do not remove the record or
invoke a second standalone server to bypass that fence.

The explicit maintenance entry point is
`python -m hermes_cli.local_runtime.reconciliation`, executed through the normal
protected Hermes Python environment. It never launches or signals a process,
loads a model, performs inference, changes ESTOP or starts a gateway.

## Prospective checkpoint

`checkpoint --expected-sha256 <reviewed-state-digest>` captures the exact complete
modern router record, native host and kernel boot-session UUIDs, persistent local
volume/file identity and reconciliation/proof-producer source identity. It holds
the same transition and cross-process boot locks as ordinary startup. The result
contains a checkpoint ID; it grants no same-boot replacement authority.

Native identity errors, malformed or aliased records, changed bytes and lock
contention refuse. Legacy or incomplete pre-spawn records are not supported by
this first interface. Kernel and filesystem identity use the maintained Darwin
native providers; no wall-clock, environment or hostname fallback exists.

A historical restart receipt that did not bind this exact record is insufficient.
For an old record without genuine prospective evidence, this operation cannot
retroactively certify an earlier reboot. Any needed maintenance/restart window
must be separately planned and approved; the CLI does not request or perform one.

## Reconcile after the boundary

`reconcile --expected-sha256 <same-digest> --checkpoint-id <returned-ID>` requires
the same host, a different native kernel boot session, unchanged persistent record
binding and matching qualified source. It preserves the original `server.json`
in place and first stores a byte-identical recovery copy in an owner-only directory
(0700 directory, 0600 file). The original can contain a local API key; the recovery
copy is private user state and must not be attached to an issue or PR.

An immutable retirement receipt is published only after the copy and all fences
are verified. Partial/failed publication supplies no replacement authority.
Checkpoint and retirement evidence is keyed by the record digest, with a separate
recovery directory per checkpoint. Later router incarnations can obtain their own
proof without overwriting earlier audit evidence.

Reconciliation is a live state write requiring its own exact operator approval;
source tests or a card-creation grant do not authorize invoking it.

## Separately authorized next startup

Only the ordinary startup path, while holding the actual boot lock, consumes a
matching retirement proof. It may atomically replace the exact retired state with
a fresh unverified pre-spawn intent. It never removes `server.json` first. If
replacement fails, the old state or the new unverified fence remains present; the
private original remains available. Changed records, recovery files, permissions,
source, host or proof refuse. A recycled PID is neither adopted nor signalled.

The fresh incarnation does not inherit its predecessor's retirement authority.
Any later uncertain failure needs fresh evidence. Do not automatically replay an
uncertain operation or restore old state over a newly published owner record.

Server/model startup, local-route readiness, card admission/execution and global
control changes remain separate approvals. Reconciliation alone proves none of
them, and performs none of them.
