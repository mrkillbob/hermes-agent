---
title: Atomic fleet claim recovery
description: "Versioned claim outcomes, explicit legacy cutover, runner persistence and qualification limits"
---

# Atomic fleet claim recovery

The optional claim recovery protocol resolves ambiguous claim acknowledgements without sending another claim request or replaying an admitted execution. It is disabled by default. Enable it explicitly with `FleetRunner(..., claim_recovery=True)` or the standalone runner's `--claim-recovery` flag, with a persistent state directory. Enabling the flag does not perform coordinator cutover.

## Coordinator protocol

The existing bearer-authenticated server exposes these routes under `/v2/fleet/claim-recovery/`:

| Method | Route | Effect |
| --- | --- | --- |
| GET | `capabilities` | Read protocol version, stable store identity and any established cutover receipt. |
| GET | `outcome?request=...` | Read the exact canonical request's outcome; absence remains absence. |
| POST | `cutover` | Explicitly establish the store-wide legacy claim fence for the expected store identity. |
| POST | `claim` | Atomically select at most one claim and record an immutable outcome. |
| POST | `resolve` | Return an existing outcome or permanently seal an absent request without selecting work. |

A request binds its nonce to the protocol version, store/cutover/installation identity, runner capability, request time and lease duration. Nonce collisions with different arguments fail. Outcomes distinguish `claimed`, `empty`, `sealed_empty` and read-only `absent`. Claimed outcomes retain the original claim's task, claim, node, profile, attempt and lease snapshot; they contain no task body, prompt or credential. Ledger entries have no expiry or retirement API. New clients require strict version, request binding and bounded complete response framing; they do not fall back to legacy claiming.

The `FleetStore` and `FleetClient` methods are `claim_recovery_capabilities()`, `establish_claim_cutover(expected_store_id)`, `claim_request(request)`, `lookup_claim_request(request)` and `resolve_claim_request(request)`.

## Explicit cutover and compatibility

Cutover affects the entire coordinator store. Its SQLite writer transaction refuses any running or uncertain task, or nonzero active load. It never releases uncertain work. Successful cutover permanently closes legacy claims through this API and returns a store-bound receipt. A persistent trigger also rejects delayed legacy claim transactions from unchanged old code. New reservations must bind the exact task, claim, node, profile and attempt; stale nonce residue cannot authorize legacy work.

Cutover is a separately authorized maintenance action requiring quiescence. Runners never invoke it automatically. An empty new local journal is not evidence that legacy work is clear. Without a valid explicit producer receipt, recovery mode holds before claiming. Legacy compatibility mode retains its existing behavior before cutover; old clients cannot claim after the fence.

## Runner persistence and settlement

An exclusively owned local SQLite journal pins installation and coordinator identity. One unresolved intent spans every configured profile and is committed before the single claim send. Restart uses lookup and, if absent, resolve-or-seal. It never resends the claim request. Corruption, missing state, changed identity, unsupported protocol or another owner preserve a hold.

An executing transition commits before external execution. Recovery checks the exact current claim and lease renewal. Retained admission evidence takes precedence over a prelaunch-looking record: it reconciles the original admission and does not launch it again. Unknown execution is held; recovery neither requeues work nor treats an arbitrary terminal task record as proof of settled admission.

Terminal delivery ordering is coordinator acknowledgement, exact local journal closure, then capture/delivery cleanup. A failure before local closure retains the exact delivery and original proof for idempotent settlement. A failure after closure leaves cleanup work without reopening or replaying the execution. Persisted recovery state also prevents silently disabling the mode to bypass an unresolved intent. New captures use a nested namespace; unexplained legacy journals require explicit reconciliation.

## Qualification limits

Inert tests exercise transaction races, acknowledgement loss, malformed protocol messages, ownership, unchanged legacy SQL, fault-point reopen, admission precedence and settlement cleanup ordering. They use temporary SQLite, fake transports and inert executors. They are not native process-kill, power-loss, Windows filesystem/locking or real supervisor-topology qualification.

The protocol assumes intact, durable, non-rolled-back coordinator and local storage, plus exclusive, uncloned installation state. Arbitrary coherent database restore retaining the same identities is not detected. Installing this source does not qualify or activate a deployment, perform cutover, change configuration or release existing uncertain work. Native runtime qualification remains a separate gate.
