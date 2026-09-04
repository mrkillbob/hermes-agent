"""Bounded standard-library operations that never require interpretation."""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo


_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMPARISONS: dict[str, Callable[[object, object], bool]] = {
    "<": lambda left, right: left < right,
    "<=": lambda left, right: left <= right,
    "==": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    ">=": lambda left, right: left >= right,
    ">": lambda left, right: left > right,
}


def _contained(path: Path, allowed_root: Path) -> Path:
    root = allowed_root.resolve(strict=True)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("path is outside the allowed root") from exc
    return resolved


def hash_file(path: Path, *, allowed_root: Path, max_bytes: int = 2_000_000) -> str:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    resolved = _contained(path, allowed_root)
    if not resolved.is_file():
        raise ValueError("hash target is not a regular file")
    if resolved.stat().st_size > max_bytes:
        raise ValueError("hash target exceeds size bound")
    digest = hashlib.sha256()
    observed = 0
    with resolved.open("rb") as handle:
        while chunk := handle.read(min(65_536, max_bytes + 1 - observed)):
            observed += len(chunk)
            if observed > max_bytes:
                raise ValueError("hash target exceeds size bound")
            digest.update(chunk)
    return digest.hexdigest()


def content_changed(previous_sha256: str, current_content: bytes) -> bool:
    if not _SHA256.fullmatch(previous_sha256):
        raise ValueError("previous content checksum is invalid")
    return hashlib.sha256(current_content).hexdigest() != previous_sha256


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def compare_timestamps(left: str, right: str, operator: str) -> bool:
    comparator = _COMPARISONS.get(operator)
    if comparator is None:
        raise ValueError("unsupported timestamp comparator")
    return comparator(_timestamp(left), _timestamp(right))


def deduplicate_exact_ids(
    rows: Sequence[Mapping[str, object]],
    id_field: str,
    *,
    max_rows: int = 10_000,
) -> tuple[tuple[Mapping[str, object], ...], int]:
    if not id_field or len(id_field) > 128:
        raise ValueError("id field is invalid")
    if len(rows) > max_rows:
        raise ValueError("deduplication input exceeds row bound")
    seen: set[object] = set()
    unique: list[Mapping[str, object]] = []
    duplicates = 0
    for row in rows:
        if id_field not in row or row[id_field] is None:
            raise ValueError(f"row is missing exact id field {id_field}")
        identity = row[id_field]
        try:
            already_seen = identity in seen
        except TypeError as exc:
            raise ValueError("exact id must be hashable") from exc
        if already_seen:
            duplicates += 1
        else:
            seen.add(identity)
            unique.append(row)
    return tuple(unique), duplicates


def _decimal(value: Decimal | str | int) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric value is not a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError("numeric value is not a finite decimal")
    return parsed


def decimal_arithmetic(
    left: Decimal | str | int,
    operator: str,
    right: Decimal | str | int,
) -> Decimal:
    lhs = _decimal(left)
    rhs = _decimal(right)
    if operator == "+":
        return lhs + rhs
    if operator == "-":
        return lhs - rhs
    if operator == "*":
        return lhs * rhs
    if operator == "/":
        return lhs / rhs
    raise ValueError("unsupported decimal operator")


def calculate_revenue(values: Iterable[Decimal | str | int]) -> Decimal:
    return sum((_decimal(value) for value in values), start=Decimal("0"))


def threshold_compare(
    value: Decimal | str | int,
    operator: str,
    threshold: Decimal | str | int,
) -> bool:
    comparator = _COMPARISONS.get(operator)
    if comparator is None:
        raise ValueError("unsupported threshold comparator")
    return comparator(_decimal(value), _decimal(threshold))


def experiment_metrics(
    *,
    exposures: int,
    conversions: int,
    revenue: Decimal | str | int,
) -> dict[str, Decimal]:
    if isinstance(exposures, bool) or not isinstance(exposures, int) or exposures <= 0:
        raise ValueError("exposures must be a positive integer")
    if (
        isinstance(conversions, bool)
        or not isinstance(conversions, int)
        or not 0 <= conversions <= exposures
    ):
        raise ValueError("conversions must be between zero and exposures")
    revenue_value = _decimal(revenue)
    if revenue_value < 0:
        raise ValueError("revenue cannot be negative")
    denominator = Decimal(exposures)
    return {
        "conversion_rate": Decimal(conversions) / denominator,
        "revenue_per_exposure": revenue_value / denominator,
    }


def read_only_sqlite_query(
    database: Path,
    query: str,
    parameters: Sequence[object] = (),
    *,
    allowed_root: Path,
    max_rows: int = 100,
) -> list[dict[str, object]]:
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= 1_000:
        raise ValueError("max_rows must be between one and 1000")
    statement = query.strip()
    if ";" in statement:
        raise ValueError("SQLite query must be a single statement")
    if not re.match(r"(?is)^(select|with)\b", statement):
        raise ValueError("SQLite query must start with SELECT or WITH")
    resolved = _contained(database, allowed_root)
    if not resolved.is_file():
        raise ValueError("SQLite database is unavailable")
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        cursor = connection.execute(statement, tuple(parameters))
        rows = cursor.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            raise ValueError("SQLite result exceeds row bound")
        return [dict(row) for row in rows]
    finally:
        connection.close()


def loopback_health_check(
    url: str,
    *,
    expected_status: int = 200,
    timeout_seconds: float = 2.0,
    connection_factory: Callable[..., object] = http.client.HTTPConnection,
) -> bool:
    parsed = urlparse(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("health endpoint must be uncredentialed loopback HTTP")
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    connection = connection_factory(parsed.hostname, port, timeout_seconds)
    try:
        connection.request("GET", path, {"Accept": "application/json"})
        response = connection.getresponse()
        return int(response.status) == expected_status
    except (OSError, TimeoutError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def cpu_load_snapshot(
    *,
    load_values: tuple[float, float, float] | None = None,
    cpu_count: int | None = None,
) -> dict[str, Decimal | int]:
    values = load_values if load_values is not None else os.getloadavg()
    processors = cpu_count if cpu_count is not None else os.cpu_count()
    if processors is None or processors <= 0 or len(values) != 3:
        raise ValueError("CPU load evidence is unavailable")
    load_1m = _decimal(str(values[0]))
    return {
        "cpu_count": processors,
        "load_1m": load_1m,
        "normalized_load_1m": load_1m / Decimal(processors),
    }


def memory_free_percent(memory_pressure_text: str) -> Decimal:
    match = re.search(r"System-wide memory free percentage:\s*([0-9.]+)%", memory_pressure_text)
    if not match:
        raise ValueError("RAM free-memory evidence is unavailable")
    value = _decimal(match.group(1))
    if not Decimal("0") <= value <= Decimal("100"):
        raise ValueError("RAM free-memory percentage is invalid")
    return value


def _clock(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("schedule time must be HH:MM") from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValueError("schedule time must be minute precision without timezone")
    return parsed


def within_schedule(
    observed_at: datetime,
    *,
    timezone_name: str,
    weekdays: set[int],
    start: str,
    end: str,
) -> bool:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("schedule observation must be timezone-aware")
    if not weekdays or not weekdays <= set(range(7)):
        raise ValueError("schedule weekdays are invalid")
    local = observed_at.astimezone(ZoneInfo(timezone_name))
    if local.weekday() not in weekdays:
        return False
    start_time = _clock(start)
    end_time = _clock(end)
    current = local.time().replace(tzinfo=None)
    if start_time <= end_time:
        return start_time <= current < end_time
    return current >= start_time or current < end_time
