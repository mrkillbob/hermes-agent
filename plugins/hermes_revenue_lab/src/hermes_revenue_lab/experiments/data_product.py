"""HRL-9 private NHTSA model-year recall intelligence experiment."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from hermes_revenue_lab.ledger.types import parse_timestamp
from hermes_revenue_lab.provenance import (
    ArtifactRunStore,
    ModelUsage,
    RunManifest,
    RunVerdict,
    SourceRecord,
)

_NHTSA_ENDPOINT = "https://api.nhtsa.gov/recalls/recallsByVehicle"
_NHTSA_LICENSE = "https://www.usa.gov/government-works"
_NHTSA_USE_POLICY = "https://api.nhtsa.gov/"
_CAMPAIGN = re.compile(r"[0-9A-Z-]{5,32}")
_ACTION = re.compile(r"[0-9A-Z-]{1,32}")
_REQUIRED_RESULT_FIELDS = {
    "Manufacturer",
    "NHTSACampaignNumber",
    "parkIt",
    "parkOutSide",
    "overTheAirUpdate",
    "NHTSAActionNumber",
    "ReportReceivedDate",
    "Component",
    "Summary",
    "Consequence",
    "Remedy",
    "Notes",
    "ModelYear",
    "Make",
    "Model",
}


@dataclass(frozen=True)
class DataExperiment:
    experiment_id: str
    customer: str
    recurring_problem: str
    update_cadence: str
    safety_limitation: str
    publication_status: str


SELECTED_DATA_EXPERIMENT = DataExperiment(
    experiment_id="independent_used_car_dealer_model_recall_watch",
    customer="independent used-car dealers",
    recurring_problem=(
        "Model-year recall records change over time and are costly to re-check across an inventory mix."
    ),
    update_cadence="daily_bounded_watchlist",
    safety_limitation=(
        "This is not VIN-specific, not proof that a particular vehicle is affected, and not a "
        "substitute for the official NHTSA VIN lookup or a dealer safety process."
    ),
    publication_status="private_draft_only",
)


@dataclass(frozen=True)
class MonetizationHypothesis:
    mode: str
    status: str = "hypothesis"


MONETIZATION_HYPOTHESES = tuple(
    MonetizationHypothesis(mode)
    for mode in (
        "one_time_csv",
        "paid_report",
        "alert_subscription",
        "business_subscription",
        "api",
    )
)


def _bounded_text(name: str, value: object, *, maximum: int = 20_000) -> str:
    if not isinstance(value, str):
        raise TypeError(f"NHTSA {name} is invalid")
    normalized = value.strip()
    if not 1 <= len(normalized) <= maximum:
        raise ValueError(f"NHTSA {name} is invalid")
    return normalized


@dataclass(frozen=True)
class VehicleQuery:
    model_year: int
    make: str
    model: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.model_year, bool)
            or not isinstance(self.model_year, int)
            or not 1980 <= self.model_year <= 2100
        ):
            raise ValueError("vehicle model year is invalid")
        for name in ("make", "model"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 80
                or any(character in value for character in "\r\n\x00")
            ):
                raise ValueError(f"vehicle {name} is invalid")

    def canonical_record(self) -> dict[str, object]:
        return {"model_year": self.model_year, "make": self.make, "model": self.model}


def build_nhtsa_url(query: VehicleQuery) -> str:
    return (
        _NHTSA_ENDPOINT
        + "?"
        + urlencode(
            {
                "make": query.make,
                "model": query.model,
                "modelYear": str(query.model_year),
            }
        )
    )


_Transport = Callable[[str, float], bytes]


def _default_transport(url: str, timeout: float) -> bytes:
    request = Request(
        url, headers={"User-Agent": "HermesRevenueLab/0.1 (bounded HRL-9 pilot)"}
    )
    with urlopen(request, timeout=timeout) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname != "api.nhtsa.gov":
            raise ValueError("NHTSA collector redirected outside the allowed host")
        if response.status != 200:
            raise ValueError("NHTSA collector received a non-success status")
        if response.headers.get_content_type() != "application/json":
            raise ValueError("NHTSA collector received a non-JSON response")
        content_length = response.headers.get("Content-Length")
        if content_length is not None and int(content_length) > 5_000_000:
            raise ValueError("NHTSA payload size is invalid")
        return response.read(5_000_001)


def fetch_nhtsa_payload(
    query: VehicleQuery,
    *,
    transport: _Transport = _default_transport,
    timeout: float = 8.0,
) -> bytes:
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 < timeout <= 30
    ):
        raise ValueError("NHTSA timeout is invalid")
    payload = transport(build_nhtsa_url(query), float(timeout))
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= 5_000_000:
        raise ValueError("NHTSA payload size is invalid")
    return payload


@dataclass(frozen=True)
class RecallRecord:
    record_id: str
    campaign_number: str
    manufacturer: str
    model_year: int
    make: str
    model: str
    action_number: str
    report_received_date: str
    component: str
    summary: str
    consequence: str
    remedy: str
    notes: str
    park_it: bool
    park_outside: bool
    over_the_air_update: bool
    record_sha256: str

    def canonical_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ScoredRecall:
    record: RecallRecord
    urgency_score: int
    reason_codes: tuple[str, ...]

    def canonical_record(self) -> dict[str, object]:
        return {
            **self.record.canonical_record(),
            "urgency_score": self.urgency_score,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class RecallDigest:
    product_id: str
    generated_at: str
    query: VehicleQuery
    status: str
    disclaimer: str
    added_count: int
    changed_count: int
    unchanged_count: int
    items: tuple[ScoredRecall, ...]
    source_id: str
    source_url: str
    source_content_sha256: str
    source_license: str = _NHTSA_LICENSE
    source_use_policy: str = _NHTSA_USE_POLICY

    def canonical_record(self) -> dict[str, object]:
        return {
            "schema_version": "hrl.nhtsa_recall_digest.v1",
            "product_id": self.product_id,
            "generated_at": self.generated_at,
            "query": self.query.canonical_record(),
            "status": self.status,
            "disclaimer": self.disclaimer,
            "added_count": self.added_count,
            "changed_count": self.changed_count,
            "unchanged_count": self.unchanged_count,
            "items": [
                {
                    **item.canonical_record(),
                    "provenance": {
                        "source_id": self.source_id,
                        "source_url": self.source_url,
                        "source_content_sha256": self.source_content_sha256,
                        "collected_at": self.generated_at,
                    },
                }
                for item in self.items
            ],
            "source_license": self.source_license,
            "source_use_policy": self.source_use_policy,
        }


@dataclass(frozen=True)
class RecallUpdate:
    query: VehicleQuery
    source: SourceRecord
    added_record_ids: tuple[str, ...]
    changed_record_ids: tuple[str, ...]
    unchanged_record_ids: tuple[str, ...]
    digest: RecallDigest


def _record_payload(
    row: Mapping[str, object], query: VehicleQuery
) -> dict[str, object]:
    if set(row) != _REQUIRED_RESULT_FIELDS:
        raise ValueError("NHTSA result fields do not match the expected schema")
    try:
        model_year = int(str(row["ModelYear"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("NHTSA result model year is invalid") from exc
    make = _bounded_text("make", row["Make"], maximum=80).upper()
    model = _bounded_text("model", row["Model"], maximum=80).upper()
    if (model_year, make, model) != (
        query.model_year,
        query.make.upper(),
        query.model.upper(),
    ):
        raise ValueError("NHTSA result is outside the requested vehicle scope")
    campaign = _bounded_text(
        "campaign number", row["NHTSACampaignNumber"], maximum=32
    ).upper()
    if not _CAMPAIGN.fullmatch(campaign):
        raise ValueError("NHTSA campaign number is invalid")
    action = _bounded_text(
        "action number", row["NHTSAActionNumber"], maximum=32
    ).upper()
    if not _ACTION.fullmatch(action):
        raise ValueError("NHTSA action number is invalid")
    date_text = _bounded_text("report date", row["ReportReceivedDate"], maximum=10)
    try:
        day, month, year = (int(part) for part in date_text.split("/"))
        parsed_date = date(year, month, day).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("NHTSA report date is invalid") from exc
    booleans = {}
    for source_name, target_name in (
        ("parkIt", "park_it"),
        ("parkOutSide", "park_outside"),
        ("overTheAirUpdate", "over_the_air_update"),
    ):
        value = row[source_name]
        if not isinstance(value, bool):
            raise TypeError(f"NHTSA {source_name} is invalid")
        booleans[target_name] = value
    return {
        "record_id": f"{campaign}:{model_year}:{make}:{model}",
        "campaign_number": campaign,
        "manufacturer": _bounded_text("manufacturer", row["Manufacturer"]),
        "model_year": model_year,
        "make": make,
        "model": model,
        "action_number": action,
        "report_received_date": parsed_date,
        "component": _bounded_text("component", row["Component"]),
        "summary": _bounded_text("summary", row["Summary"]),
        "consequence": _bounded_text("consequence", row["Consequence"]),
        "remedy": _bounded_text("remedy", row["Remedy"]),
        "notes": _bounded_text("notes", row["Notes"]),
        **booleans,
    }


def _normalize(row: Mapping[str, object], query: VehicleQuery) -> RecallRecord:
    values = _record_payload(row, query)
    digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RecallRecord(**values, record_sha256=digest)


def _score(record: RecallRecord) -> ScoredRecall:
    score = 0
    reasons: list[str] = []
    combined = f"{record.summary} {record.consequence}".lower()
    if record.park_it:
        score += 40
        reasons.append("park_it")
    if record.park_outside:
        score += 30
        reasons.append("park_outside")
    if "death" in combined:
        score += 30
        reasons.append("death")
    elif "injury" in combined:
        score += 20
        reasons.append("injury")
    elif "fire" in combined:
        score += 15
        reasons.append("fire")
    elif "crash" in combined:
        score += 10
        reasons.append("crash")
    return ScoredRecall(record, min(score, 100), tuple(reasons or ("official_recall",)))


class NHTSARecallHistory:
    def __init__(self, database: Path, *, allowed_root: Path) -> None:
        root = allowed_root.resolve(strict=True)
        resolved = database.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "NHTSA history database is outside the allowed root"
            ) from exc
        if database.is_symlink():
            raise ValueError("NHTSA history database cannot be a symlink")
        if resolved.exists() and not resolved.is_file():
            raise ValueError("NHTSA history database is not a regular file")
        resolved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        resolved.parent.chmod(0o700)
        self.database = resolved
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS recall_versions (
                    record_id TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    raw_content_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (record_id, record_sha256)
                );
                CREATE TABLE IF NOT EXISTS recall_current (
                    record_id TEXT PRIMARY KEY,
                    record_sha256 TEXT NOT NULL
                );
                """
            )
        self.database.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def apply(
        self,
        records: Sequence[RecallRecord],
        *,
        observed_at: str,
        raw_content_sha256: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        parse_timestamp(observed_at)
        added: list[str] = []
        changed: list[str] = []
        unchanged: list[str] = []
        with self._connect() as connection:
            for record in records:
                row = connection.execute(
                    "SELECT record_sha256 FROM recall_current WHERE record_id = ?",
                    (record.record_id,),
                ).fetchone()
                if row is None:
                    added.append(record.record_id)
                elif row["record_sha256"] == record.record_sha256:
                    unchanged.append(record.record_id)
                    continue
                else:
                    changed.append(record.record_id)
                connection.execute(
                    "INSERT OR IGNORE INTO recall_versions VALUES (?, ?, ?, ?, ?)",
                    (
                        record.record_id,
                        record.record_sha256,
                        observed_at,
                        raw_content_sha256,
                        json.dumps(
                            record.canonical_record(),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                connection.execute(
                    "INSERT INTO recall_current VALUES (?, ?) "
                    "ON CONFLICT(record_id) DO UPDATE SET record_sha256=excluded.record_sha256",
                    (record.record_id, record.record_sha256),
                )
        return tuple(added), tuple(changed), tuple(unchanged)

    def version_count(self, record_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM recall_versions WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        return int(row["count"])

    def total_versions(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM recall_versions"
            ).fetchone()
        return int(row["count"])


def process_nhtsa_payload(
    *,
    query: VehicleQuery,
    raw_payload: bytes,
    collected_at: str,
    history: NHTSARecallHistory,
) -> RecallUpdate:
    parse_timestamp(collected_at)
    if not isinstance(raw_payload, bytes) or not 1 <= len(raw_payload) <= 5_000_000:
        raise ValueError("NHTSA payload size is invalid")
    try:
        document = json.loads(raw_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NHTSA payload is not valid JSON") from exc
    if not isinstance(document, Mapping) or set(document) != {
        "Count",
        "Message",
        "results",
    }:
        raise ValueError("NHTSA response schema is invalid")
    rows = document["results"]
    count = document["Count"]
    _bounded_text("message", document["Message"], maximum=500)
    if (
        not isinstance(rows, list)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != len(rows)
        or count > 5_000
    ):
        raise ValueError("NHTSA response count is invalid")
    normalized: dict[str, RecallRecord] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("NHTSA result must be an object")
        record = _normalize(row, query)
        existing = normalized.get(record.record_id)
        if existing is not None and existing.record_sha256 != record.record_sha256:
            raise ValueError("NHTSA response contains a conflicting duplicate record")
        normalized[record.record_id] = record
    records = tuple(normalized[key] for key in sorted(normalized))
    raw_digest = hashlib.sha256(raw_payload).hexdigest()
    added, changed, unchanged = history.apply(
        records, observed_at=collected_at, raw_content_sha256=raw_digest
    )
    source_url = build_nhtsa_url(query)
    source = SourceRecord(
        source_id=f"nhtsa-{query.model_year}-{hashlib.sha256(source_url.encode()).hexdigest()[:16]}",
        locator=source_url,
        source_kind="public_api",
        collected_at=collected_at,
        content_sha256=raw_digest,
        permission_basis="us_government_work",
        license_status="permitted",
        terms_status="permitted",
        robots_status="not_applicable",
    )
    scored = tuple(
        sorted(
            (_score(record) for record in records),
            key=lambda item: item.record.record_id,
        )
    )
    digest = RecallDigest(
        product_id=SELECTED_DATA_EXPERIMENT.experiment_id,
        generated_at=collected_at,
        query=query,
        status="private_draft",
        disclaimer=SELECTED_DATA_EXPERIMENT.safety_limitation,
        added_count=len(added),
        changed_count=len(changed),
        unchanged_count=len(unchanged),
        items=scored,
        source_id=source.source_id,
        source_url=source.locator,
        source_content_sha256=source.content_sha256,
    )
    return RecallUpdate(query, source, added, changed, unchanged, digest)


def publish_update_run(
    *,
    store: ArtifactRunStore,
    manifest: RunManifest,
    update: RecallUpdate,
    model_usage: Sequence[ModelUsage],
    verdict: RunVerdict,
) -> Path:
    if manifest.experiment_id != SELECTED_DATA_EXPERIMENT.experiment_id:
        raise ValueError("run manifest is not bound to the selected HRL-9 experiment")
    return store.write_run(
        manifest=manifest,
        inputs={"vehicle_query": update.query.canonical_record(), "vin_lookup": False},
        sources=(update.source,),
        model_usage=model_usage,
        outputs={"recall_digest.json": update.digest.canonical_record()},
        logs={
            "update.log": (
                f"added={len(update.added_record_ids)} changed={len(update.changed_record_ids)} "
                f"unchanged={len(update.unchanged_record_ids)}\n"
            )
        },
        verdict=verdict,
    )
