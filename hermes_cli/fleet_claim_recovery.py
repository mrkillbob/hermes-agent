"""Atomic immutable claim outcomes and an explicit store-wide legacy barrier.

No TTL pruning or automatic cutover: a cutover requires quiescence and permanently
closes legacy SQL, including requests delayed in an older process. Database rollback
and running old code against a restored pre-cutover database are outside this proof.
"""

from dataclasses import asdict
import json
import secrets

from hermes_cli.fleet_protocol import (
    CLAIM_RECOVERY_VERSION,
    ClaimOutcome,
    ClaimRequest,
    ProtocolError,
    recovery_id,
)


def initialize(connection):
    existed = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='fleet_recovery_meta'"
    ).fetchone()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS fleet_recovery_meta (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            store_id TEXT NOT NULL, cutover_id TEXT
        );
        CREATE TABLE IF NOT EXISTS fleet_recovery_owners (
            node_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS fleet_recovery_owner_update
        BEFORE UPDATE ON fleet_recovery_owners
        BEGIN SELECT RAISE(ABORT, 'immutable recovery owner'); END;
        CREATE TRIGGER IF NOT EXISTS fleet_recovery_owner_delete
        BEFORE DELETE ON fleet_recovery_owners
        BEGIN SELECT RAISE(ABORT, 'immutable recovery owner'); END;
        CREATE TABLE IF NOT EXISTS fleet_claim_requests (
            nonce TEXT PRIMARY KEY, request_json TEXT NOT NULL,
            task_id TEXT, claim_id TEXT, node_id TEXT, profile TEXT, attempt INTEGER,
            outcome_json TEXT
        );
        CREATE TRIGGER IF NOT EXISTS fleet_outcome_immutable_update
        BEFORE UPDATE ON fleet_claim_requests WHEN OLD.outcome_json IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'immutable claim outcome'); END;
        CREATE TRIGGER IF NOT EXISTS fleet_outcome_immutable_delete
        BEFORE DELETE ON fleet_claim_requests
        BEGIN SELECT RAISE(ABORT, 'immutable claim outcome'); END;
        CREATE TRIGGER IF NOT EXISTS fleet_recovery_meta_delete
        BEFORE DELETE ON fleet_recovery_meta
        BEGIN SELECT RAISE(ABORT, 'immutable recovery identity'); END;
        CREATE TRIGGER IF NOT EXISTS fleet_recovery_meta_update
        BEFORE UPDATE ON fleet_recovery_meta
        WHEN OLD.store_id != NEW.store_id OR OLD.cutover_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'immutable recovery identity'); END;
        CREATE TRIGGER IF NOT EXISTS fleet_legacy_claim_fence
        BEFORE UPDATE ON fleet_tasks
        WHEN NEW.status='running' AND OLD.status='pending'
          AND (SELECT cutover_id FROM fleet_recovery_meta WHERE singleton=1) IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM fleet_claim_requests r
            WHERE r.nonce=NEW.claim_request_nonce AND r.outcome_json IS NULL
              AND r.task_id=NEW.task_id AND r.claim_id=NEW.claim_id
              AND r.node_id=NEW.node_id AND r.profile=NEW.runner_profile
              AND r.attempt=NEW.attempt
          )
        BEGIN SELECT RAISE(ABORT, 'legacy claims permanently closed'); END;
    """)
    if not existed:
        connection.execute(
            "INSERT INTO fleet_recovery_meta VALUES (1, ?, NULL)", (secrets.token_hex(32),)
        )
    meta(connection)


def meta(connection):
    rows = connection.execute("SELECT * FROM fleet_recovery_meta").fetchall()
    if len(rows) != 1 or rows[0]["singleton"] != 1:
        raise ProtocolError("missing recovery store identity")
    row = rows[0]
    recovery_id(row["store_id"], "store_id")
    if row["cutover_id"] is not None:
        recovery_id(row["cutover_id"], "cutover_id")
    return row


def capabilities(store):
    with store._connection() as connection:
        row = meta(connection)
        cutover = (
            None
            if row["cutover_id"] is None
            else {
                "version": CLAIM_RECOVERY_VERSION,
                "store_id": row["store_id"],
                "cutover_id": row["cutover_id"],
                "scope": "store-wide",
                "legacy_claims": "permanently_closed",
            }
        )
        return {
            "version": CLAIM_RECOVERY_VERSION,
            "store_id": row["store_id"],
            "cutover": cutover,
            "resolve_or_seal": True,
        }


def establish_cutover(store, expected_store_id):
    recovery_id(expected_store_id, "store_id")
    with store._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = meta(connection)
        if row["store_id"] != expected_store_id:
            raise ProtocolError("coordinator store identity mismatch")
        if row["cutover_id"] is None:
            busy = connection.execute(
                "SELECT 1 FROM fleet_tasks WHERE status IN ('running','uncertain') LIMIT 1"
            ).fetchone()
            load = connection.execute(
                "SELECT 1 FROM fleet_runners WHERE active_load != 0 LIMIT 1"
            ).fetchone()
            if busy or load:
                raise ProtocolError(
                    "store must be quiescent; no running/uncertain claims or active load"
                )
            connection.execute(
                "UPDATE fleet_recovery_meta SET cutover_id=? WHERE singleton=1",
                (secrets.token_hex(32),),
            )
    return capabilities(store)


def _validate(connection, request):
    if not isinstance(request, ClaimRequest):
        raise ProtocolError("typed claim request required")
    # Revalidate even a directly constructed request at the storage boundary.
    ClaimRequest.from_dict(request.to_dict())
    row = meta(connection)
    if row["store_id"] != request.store_id or row["cutover_id"] != request.cutover_id:
        raise ProtocolError("claim cutover or store identity mismatch")
    owner = connection.execute(
        "SELECT installation_id FROM fleet_recovery_owners WHERE node_id=?",
        (request.capability.node_id,),
    ).fetchone()
    if owner is not None and owner["installation_id"] != request.installation_id:
        raise ProtocolError("runner installation identity mismatch")


def _existing(connection, request):
    row = connection.execute(
        "SELECT * FROM fleet_claim_requests WHERE nonce=?", (request.nonce,)
    ).fetchone()
    if row is None:
        return None
    if ClaimRequest.from_dict(json.loads(row["request_json"])) != request:
        raise ProtocolError("nonce binding collision")
    if row["outcome_json"] is None:
        raise ProtocolError("incomplete committed claim outcome")
    return ClaimOutcome.from_dict(json.loads(row["outcome_json"]), request=request)


def outcome(store, request, *, operation):
    if operation not in {"claim", "lookup", "resolve"}:
        raise ProtocolError("unsupported recovery operation")
    with store._connection() as connection:
        connection.execute("BEGIN" if operation == "lookup" else "BEGIN IMMEDIATE")
        _validate(connection, request)
        previous = _existing(connection, request)
        if previous is not None:
            return previous
        if operation == "lookup":
            return ClaimOutcome(request, "absent")
        connection.execute(
            "INSERT OR IGNORE INTO fleet_recovery_owners VALUES (?,?)",
            (request.capability.node_id, request.installation_id),
        )
        connection.execute(
            "INSERT INTO fleet_claim_requests(nonce,request_json) VALUES (?,?)",
            (request.nonce, json.dumps(request.to_dict(), sort_keys=True, allow_nan=False)),
        )
        claim = None
        if operation == "claim":
            claim = store._claim_in_transaction(
                connection,
                request.capability,
                now=request.now,
                lease_seconds=request.lease_seconds,
                request_nonce=request.nonce,
            )
        result = ClaimOutcome(
            request,
            "claimed" if claim else ("empty" if operation == "claim" else "sealed_empty"),
            asdict(claim) if claim else None,
        )
        connection.execute(
            "UPDATE fleet_claim_requests SET outcome_json=? WHERE nonce=?",
            (json.dumps(result.to_dict(), sort_keys=True, allow_nan=False), request.nonce),
        )
        return result
