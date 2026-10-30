"""Exclusively owned, durable before-send intents for one runner installation.

SQLite FULL transactions establish the ordered process-crash contract. Native
filesystem/power-loss qualification and protection against directory/DB rollback
remain deployment prerequisites, not guarantees inferred from these unit tests.
"""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3

from hermes_cli.fleet_protocol import (
    ClaimOutcome,
    ClaimRequest,
    ProtocolError,
    recovery_id,
    validate_recovery_capabilities,
)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(*values):
    return hashlib.sha256(_json(values).encode()).hexdigest()


def _claim_identity(claim):
    fields = ("task_id", "claim_id", "node_id", "runner_profile", "attempt")
    return tuple(claim[key] if isinstance(claim, dict) else getattr(claim, key) for key in fields)


class ClaimJournal:
    def __init__(self, directory, *, node_id, coordinator):
        if (
            not isinstance(node_id, str)
            or not node_id
            or not isinstance(coordinator, str)
            or not coordinator
        ):
            raise ProtocolError("explicit coordinator and node identity required")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "claim-intents.sqlite"
        owner_path = self.directory / "claim-owner.sqlite"
        new_owner = not owner_path.exists()
        self._owner = sqlite3.connect(
            owner_path, timeout=0, isolation_level=None, check_same_thread=False
        )
        try:
            self._owner.execute("PRAGMA synchronous=FULL")
            self._owner.execute(
                "CREATE TABLE IF NOT EXISTS owner(singleton INTEGER PRIMARY KEY CHECK(singleton=1), identity_json TEXT NOT NULL)"
            )
            if new_owner:
                if self.path.exists():
                    raise ProtocolError("owner evidence missing for existing intent journal")
                identity = {
                    "node_id": node_id,
                    "coordinator": coordinator,
                    "installation_id": secrets.token_hex(32),
                }
                self._owner.execute("INSERT INTO owner VALUES(1,?)", (_json(identity),))
            self._owner.execute("BEGIN EXCLUSIVE")
            owners = self._owner.execute("SELECT identity_json FROM owner").fetchall()
            if len(owners) != 1:
                raise ProtocolError("invalid runner owner evidence")
            identity = json.loads(owners[0][0])
            if (
                set(identity) != {"node_id", "coordinator", "installation_id"}
                or identity["node_id"] != node_id
                or identity["coordinator"] != coordinator
            ):
                raise ProtocolError("runner state ownership mismatch")
            self.installation_id = recovery_id(identity["installation_id"], "installation_id")
            self.identity = identity
            if not new_owner and not self.path.exists():
                raise ProtocolError("intent journal missing from established owner")
            with self._connection() as connection:
                if new_owner:
                    connection.executescript("""
                        CREATE TABLE journal_meta(singleton INTEGER PRIMARY KEY CHECK(singleton=1), value TEXT NOT NULL);
                        CREATE TABLE intents(nonce TEXT PRIMARY KEY, request_json TEXT NOT NULL,
                            outcome_json TEXT, stage TEXT NOT NULL, integrity TEXT NOT NULL);
                        CREATE UNIQUE INDEX one_open_intent ON intents((1)) WHERE stage!='closed';
                    """)
                    connection.execute(
                        "INSERT INTO journal_meta VALUES(1,?)",
                        (_json({**identity, "schema": 1, "store_id": None, "cutover_id": None}),),
                    )
                self._meta(connection)
            owner_path.chmod(0o600)
            self.path.chmod(0o600)
            self.active()
        except BaseException:
            self.close()
            raise

    @contextmanager
    def _connection(self):
        if self._owner is None or not self._owner.in_transaction:
            raise ProtocolError("runner state is not exclusively owned")
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _meta(self, connection):
        rows = connection.execute("SELECT value FROM journal_meta").fetchall()
        if len(rows) != 1:
            raise ProtocolError("missing intent journal metadata")
        value = json.loads(rows[0][0])
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "store_id", "cutover_id", *self.identity}
            or type(value["schema"]) is not int
            or value["schema"] != 1
            or any(value[key] != item for key, item in self.identity.items())
        ):
            raise ProtocolError("invalid intent journal metadata")
        if (value["store_id"] is None) != (value["cutover_id"] is None):
            raise ProtocolError("incomplete coordinator binding")
        if value["store_id"] is not None:
            recovery_id(value["store_id"], "store_id")
            recovery_id(value["cutover_id"], "cutover_id")
        return value

    def bind(self, capabilities):
        info = validate_recovery_capabilities(capabilities)
        receipt = info["cutover"]
        if receipt is None:
            raise ProtocolError("qualified legacy cutover receipt required")
        with self._connection() as connection:
            value = self._meta(connection)
            if value["store_id"] is not None and (
                value["store_id"] != info["store_id"]
                or value["cutover_id"] != receipt["cutover_id"]
            ):
                raise ProtocolError("coordinator store/cutover changed; hold")
            value.update(store_id=info["store_id"], cutover_id=receipt["cutover_id"])
            connection.execute("UPDATE journal_meta SET value=?", (_json(value),))

    def _validate_request(self, connection, request):
        meta = self._meta(connection)
        if (
            request.installation_id != self.installation_id
            or request.capability.node_id != self.identity["node_id"]
            or request.store_id != meta["store_id"]
            or request.cutover_id != meta["cutover_id"]
        ):
            raise ProtocolError("intent ownership/binding mismatch")

    def _active(self, connection):
        active = []
        for row in connection.execute("SELECT * FROM intents"):
            if row["stage"] not in {"intent", "claimed", "executing", "closed"}:
                raise ProtocolError("invalid intent stage")
            if row["integrity"] != _digest(row["request_json"], row["outcome_json"], row["stage"]):
                raise ProtocolError("damaged intent evidence")
            request = ClaimRequest.from_dict(json.loads(row["request_json"]))
            if row["nonce"] != request.nonce:
                raise ProtocolError("intent nonce mismatch")
            self._validate_request(connection, request)
            outcome = (
                None
                if row["outcome_json"] is None
                else ClaimOutcome.from_dict(json.loads(row["outcome_json"]), request=request)
            )
            if (
                (row["stage"] == "intent") != (outcome is None)
                or (outcome is not None and outcome.kind == "absent")
                or (row["stage"] in {"claimed", "executing"} and outcome.kind != "claimed")
            ):
                raise ProtocolError("inconsistent intent outcome")
            if row["stage"] != "closed":
                active.append({"request": request, "outcome": outcome, "stage": row["stage"]})
        if len(active) > 1:
            raise ProtocolError("multiple unresolved intents")
        return active[0] if active else None

    def active(self):
        with self._connection() as connection:
            self._meta(connection)
            return self._active(connection)

    def begin(self, request):
        with self._connection() as connection:
            self._validate_request(connection, request)
            if self._active(connection) is not None:
                raise ProtocolError("unresolved claim intent")
            raw = _json(request.to_dict())
            connection.execute(
                "INSERT INTO intents VALUES(?,?,NULL,'intent',?)",
                (request.nonce, raw, _digest(raw, None, "intent")),
            )

    def _write(self, connection, request, outcome, stage):
        raw = _json(request.to_dict())
        result = _json(outcome.to_dict())
        connection.execute(
            "UPDATE intents SET outcome_json=?,stage=?,integrity=? WHERE nonce=?",
            (result, stage, _digest(raw, result, stage), request.nonce),
        )

    def record(self, outcome):
        with self._connection() as connection:
            active = self._active(connection)
            if active is None or active["request"] != outcome.request or outcome.kind == "absent":
                raise ProtocolError("outcome does not resolve current intent")
            ClaimOutcome.from_dict(outcome.to_dict(), request=active["request"])
            if active["outcome"] is not None:
                if active["outcome"] != outcome:
                    raise ProtocolError("immutable intent outcome changed")
                return
            self._write(
                connection,
                outcome.request,
                outcome,
                "claimed" if outcome.kind == "claimed" else "closed",
            )

    def _transition(self, claim, stage):
        with self._connection() as connection:
            active = self._active(connection)
            if (
                active is None
                or active["outcome"] is None
                or _claim_identity(active["outcome"].claim) != _claim_identity(claim)
            ):
                raise ProtocolError("claim does not own current intent")
            if stage == "executing" and active["stage"] != "claimed":
                raise ProtocolError("execution already admitted; never replay")
            self._write(connection, active["request"], active["outcome"], stage)

    def mark_executing(self, claim):
        self._transition(claim, "executing")

    def finish(self, claim):
        self._transition(claim, "closed")

    def close(self):
        if self._owner is not None:
            self._owner.close()
            self._owner = None
