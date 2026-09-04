"""Private SQLite retention for HRL-7 candidates and raw source evidence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .types import ScoutCandidate, ScoutEvidence, ScoutVerdict
from .validators import evaluate_candidate


class ScoutStore:
    def __init__(self, database: Path, *, allowed_root: Path) -> None:
        root = allowed_root.resolve(strict=True)
        resolved = database.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("scout database is outside the allowed root") from exc
        if database.is_symlink():
            raise ValueError("scout database cannot be a symlink")
        resolved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        resolved.parent.chmod(0o700)
        self.database = resolved
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scout_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    scout_kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    eligible INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scout_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES scout_candidates(candidate_id),
                    source_url TEXT NOT NULL,
                    source_class TEXT NOT NULL,
                    permission_basis TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    fact_code TEXT NOT NULL,
                    fact_value TEXT NOT NULL
                );
                """
            )
        self.database.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def record(self, candidate: ScoutCandidate, verdict: ScoutVerdict) -> None:
        if verdict != evaluate_candidate(candidate):
            raise ValueError("scout verdict does not match deterministic evaluation")
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO scout_candidates VALUES (?, ?, ?, ?, ?)",
                    (
                        candidate.candidate_id,
                        candidate.scout_kind,
                        candidate.subject,
                        int(verdict.eligible),
                        json.dumps(verdict.reasons, separators=(",", ":")),
                    ),
                )
                connection.executemany(
                    "INSERT INTO scout_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            item.evidence_id,
                            candidate.candidate_id,
                            item.source_url,
                            item.source_class,
                            item.permission_basis,
                            item.collected_at,
                            item.content_sha256,
                            item.fact_code,
                            item.fact_value,
                        )
                        for item in candidate.evidence
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("scout candidate or evidence is already recorded") from exc

    def load(self, candidate_id: str) -> tuple[ScoutCandidate, ScoutVerdict]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scout_candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            evidence_rows = connection.execute(
                "SELECT * FROM scout_evidence WHERE candidate_id = ? ORDER BY rowid",
                (candidate_id,),
            ).fetchall()
        if row is None:
            raise KeyError(candidate_id)
        evidence = tuple(
            ScoutEvidence(
                evidence_id=item["evidence_id"],
                source_url=item["source_url"],
                source_class=item["source_class"],
                permission_basis=item["permission_basis"],
                collected_at=item["collected_at"],
                content_sha256=item["content_sha256"],
                fact_code=item["fact_code"],
                fact_value=item["fact_value"],
            )
            for item in evidence_rows
        )
        candidate = ScoutCandidate(
            row["candidate_id"], row["scout_kind"], row["subject"], evidence
        )
        verdict = ScoutVerdict(
            candidate_id=row["candidate_id"],
            eligible=bool(row["eligible"]),
            reasons=tuple(json.loads(row["reasons_json"])),
        )
        return candidate, verdict
