"""Private root-contained SQLite authority for HRL-5 accounting."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from .types import (
    COUNT_FIELDS,
    DURATION_FIELDS,
    MONEY_FIELDS,
    ExperimentRecord,
    PromotionEvidence,
    parse_timestamp,
)


_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")
_RECORD_FIELDS = tuple(ExperimentRecord.__dataclass_fields__)
_DECIMAL_FIELDS = set(MONEY_FIELDS + DURATION_FIELDS)


def _json(value: object) -> str:
    def default(item: object) -> str:
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError(f"cannot encode {type(item).__name__}")

    return json.dumps(value, default=default, sort_keys=True, separators=(",", ":"))


def _record_values(record: ExperimentRecord) -> tuple[object, ...]:
    values: list[object] = []
    for name in _RECORD_FIELDS:
        value = getattr(record, name)
        values.append(str(value) if isinstance(value, Decimal) else value)
    return tuple(values)


def _row_record(row: sqlite3.Row) -> ExperimentRecord:
    values: dict[str, object] = {}
    for name in _RECORD_FIELDS:
        value = row[name]
        values[name] = Decimal(value) if name in _DECIMAL_FIELDS and value is not None else value
    return ExperimentRecord(**values)


class RevenueLedger:
    def __init__(self, database: Path, *, allowed_root: Path) -> None:
        root = allowed_root.resolve(strict=True)
        resolved = database.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Revenue ledger database is outside the allowed root") from exc
        if database.is_symlink():
            raise ValueError("Revenue ledger database cannot be a symlink")
        if resolved.exists() and not resolved.is_file():
            raise ValueError("Revenue ledger database is not a regular file")
        resolved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        resolved.parent.chmod(0o700)
        self.database = resolved
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        decimal_columns = ",\n".join(
            f"{name} TEXT NULL" for name in MONEY_FIELDS + DURATION_FIELDS
        )
        count_columns = ",\n".join(f"{name} INTEGER NULL" for name in COUNT_FIELDS)
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    business_model TEXT NOT NULL,
                    niche TEXT NOT NULL,
                    status TEXT NOT NULL,
                    {decimal_columns},
                    {count_columns},
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    verdict TEXT NULL,
                    revision INTEGER NOT NULL CHECK (revision > 0)
                );
                CREATE TABLE IF NOT EXISTS promotion_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
                    kind TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    value TEXT NULL,
                    customer_relationship TEXT NULL
                );
                CREATE TABLE IF NOT EXISTS archive_findings (
                    experiment_id TEXT PRIMARY KEY REFERENCES experiments(experiment_id),
                    reason_codes TEXT NOT NULL,
                    findings TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
                    event_type TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
        self.database.chmod(0o600)

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        experiment_id: str,
        event_type: str,
        observed_at: str,
        payload: object,
    ) -> None:
        connection.execute(
            "INSERT INTO ledger_events "
            "(experiment_id, event_type, observed_at, payload_json) VALUES (?, ?, ?, ?)",
            (experiment_id, event_type, observed_at, _json(payload)),
        )

    def create_experiment(self, record: ExperimentRecord) -> None:
        columns = ", ".join(_RECORD_FIELDS) + ", revision"
        placeholders = ", ".join("?" for _ in range(len(_RECORD_FIELDS) + 1))
        try:
            with self._connect() as connection:
                connection.execute(
                    f"INSERT INTO experiments ({columns}) VALUES ({placeholders})",
                    _record_values(record) + (1,),
                )
                self._event(
                    connection,
                    record.experiment_id,
                    "experiment_created",
                    record.created_at,
                    asdict(record),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("experiment already exists or violates the ledger schema") from exc

    def get_experiment(self, experiment_id: str) -> ExperimentRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        return _row_record(row)

    def update_experiment(self, record: ExperimentRecord, *, expected_revision: int) -> None:
        if isinstance(expected_revision, bool) or expected_revision <= 0:
            raise ValueError("expected revision is invalid")
        assignments = ", ".join(f"{name} = ?" for name in _RECORD_FIELDS)
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?", (record.experiment_id,)
            ).fetchone()
            if current is None:
                raise KeyError(record.experiment_id)
            if current["revision"] != expected_revision:
                raise ValueError("ledger revision conflict")
            if current["created_at"] != record.created_at:
                raise ValueError("experiment created_at is immutable")
            if parse_timestamp(record.updated_at) <= parse_timestamp(current["updated_at"]):
                raise ValueError("experiment update must advance updated_at")
            cursor = connection.execute(
                f"UPDATE experiments SET {assignments}, revision = revision + 1 "
                "WHERE experiment_id = ? AND revision = ?",
                _record_values(record) + (record.experiment_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("ledger revision conflict")
            self._event(
                connection,
                record.experiment_id,
                "experiment_updated",
                record.updated_at,
                asdict(record),
            )

    def add_promotion_evidence(
        self,
        experiment_id: str,
        evidence: PromotionEvidence,
    ) -> None:
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone() is None:
                raise KeyError(experiment_id)
            try:
                connection.execute(
                    "INSERT INTO promotion_evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        evidence.evidence_id,
                        experiment_id,
                        evidence.kind,
                        evidence.observed_at,
                        evidence.source_ref,
                        None if evidence.value is None else str(evidence.value),
                        evidence.customer_relationship,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("promotion evidence already exists") from exc
            self._event(
                connection,
                experiment_id,
                "promotion_evidence_added",
                evidence.observed_at,
                asdict(evidence),
            )

    def list_promotion_evidence(self, experiment_id: str) -> tuple[PromotionEvidence, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT evidence_id, kind, observed_at, source_ref, value, "
                "customer_relationship FROM promotion_evidence "
                "WHERE experiment_id = ? ORDER BY observed_at, evidence_id",
                (experiment_id,),
            ).fetchall()
        return tuple(
            PromotionEvidence(
                evidence_id=row["evidence_id"],
                kind=row["kind"],
                observed_at=row["observed_at"],
                source_ref=row["source_ref"],
                value=None if row["value"] is None else Decimal(row["value"]),
                customer_relationship=row["customer_relationship"],
            )
            for row in rows
        )

    def archive_experiment(
        self,
        experiment_id: str,
        *,
        reason_codes: tuple[str, ...],
        findings: str,
        observed_at: str,
        expected_revision: int,
    ) -> None:
        if not reason_codes or any(not _REASON.fullmatch(code) for code in reason_codes):
            raise ValueError("archive reason codes are invalid")
        if not isinstance(findings, str) or not 1 <= len(findings) <= 4_000:
            raise ValueError("archive findings are invalid")
        parse_timestamp(observed_at)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
            if row is None:
                raise KeyError(experiment_id)
            if row["revision"] != expected_revision:
                raise ValueError("ledger revision conflict")
            current = _row_record(row)
            if parse_timestamp(observed_at) <= parse_timestamp(current.updated_at):
                raise ValueError("archive must advance updated_at")
            verdict = "killed:" + ",".join(reason_codes)
            archived = replace(
                current,
                status="archived",
                updated_at=observed_at,
                verdict=verdict,
            )
            assignments = ", ".join(f"{name} = ?" for name in _RECORD_FIELDS)
            cursor = connection.execute(
                f"UPDATE experiments SET {assignments}, revision = revision + 1 "
                "WHERE experiment_id = ? AND revision = ?",
                _record_values(archived) + (experiment_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("ledger revision conflict")
            connection.execute(
                "INSERT INTO archive_findings VALUES (?, ?, ?, ?)",
                (experiment_id, _json(reason_codes), findings, observed_at),
            )
            self._event(
                connection,
                experiment_id,
                "experiment_archived",
                observed_at,
                {"reason_codes": reason_codes, "findings": findings},
            )

    def get_archive_findings(self, experiment_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT reason_codes, findings, observed_at FROM archive_findings "
                "WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        return {
            "reason_codes": tuple(json.loads(row["reason_codes"])),
            "findings": row["findings"],
            "observed_at": row["observed_at"],
        }

    def event_types(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_type FROM ledger_events ORDER BY event_id"
            ).fetchall()
        return tuple(row["event_type"] for row in rows)
