# Execution-result provenance development

The `agent.execution_artifacts` module is a **disabled, host-only scaffold**.
It has no production caller, settings/environment/CLI enabler, provider adapter,
coordinator adapter, authenticated approval issuer or confinement backend. Default
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

The abstract adapters are **not confinement evidence**. Production integration
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
