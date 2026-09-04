"""Private start-token-bound registry for Revenue Lab-owned workers."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from hermes_revenue_lab.inventory.redaction import assert_publication_safe
from hermes_revenue_lab.inventory.runner import run_command
from hermes_revenue_lab.inventory.types import CommandSpec


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True)
class RevenueWorkerRecord:
    workload_id: str
    pid: int
    process_start_token: str
    workload_kind: str
    heavy: bool
    checkpoint_path: str
    registered_at: str

    @classmethod
    def from_document(cls, value: object) -> "RevenueWorkerRecord":
        if not isinstance(value, dict) or set(value) != {
            "workload_id",
            "pid",
            "process_start_token",
            "workload_kind",
            "heavy",
            "checkpoint_path",
            "registered_at",
        }:
            raise ValueError("Revenue worker record does not match the schema")
        record = cls(**value)
        record.validate()
        return record

    def validate(self) -> None:
        if not _IDENTIFIER.fullmatch(self.workload_id):
            raise ValueError("Revenue worker workload id is invalid")
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 1:
            raise ValueError("Revenue worker PID is invalid")
        if (
            not isinstance(self.process_start_token, str)
            or not self.process_start_token
            or len(self.process_start_token) > 128
        ):
            raise ValueError("Revenue worker process start token is invalid")
        if not _IDENTIFIER.fullmatch(self.workload_kind):
            raise ValueError("Revenue worker kind is invalid")
        if not isinstance(self.heavy, bool):
            raise ValueError("Revenue worker heavy flag is invalid")
        if not isinstance(self.checkpoint_path, str) or not self.checkpoint_path:
            raise ValueError("Revenue worker checkpoint path is invalid")
        if not isinstance(self.registered_at, str) or not self.registered_at:
            raise ValueError("Revenue worker registration time is invalid")


def _contained(path: Path, allowed_root: Path) -> Path:
    root = allowed_root.resolve(strict=True)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Revenue worker path is outside Revenue Lab") from exc
    return resolved


def _atomic_json(path: Path, value: object, mode: int = 0o600) -> None:
    assert_publication_safe(value)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        position = 0
        while position < len(data):
            written = os.write(descriptor, data[position:])
            if written <= 0:
                raise OSError("Revenue worker registry write did not advance")
            position += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.chmod(mode)
    os.replace(temporary, path)


def process_start_token(pid: int) -> str | None:
    result = run_command(
        CommandSpec("worker_start", ("/bin/ps", "-p", str(pid), "-o", "lstart="))
    )
    if result.status != "available":
        return None
    value = result.stdout.strip()
    return value if value and len(value) <= 128 else None


def register_worker(
    registry_directory: Path,
    record: RevenueWorkerRecord,
    *,
    allowed_root: Path,
) -> Path:
    record.validate()
    directory = _contained(registry_directory, allowed_root)
    checkpoint = _contained(Path(record.checkpoint_path), allowed_root)
    if checkpoint.is_symlink():
        raise ValueError("Revenue worker checkpoint cannot be a symlink")
    path = directory / f"{record.workload_id}.worker.json"
    if path.is_symlink():
        raise ValueError("Revenue worker registry target cannot be a symlink")
    _atomic_json(path, asdict(record))
    return path


def load_verified_workers(
    registry_directory: Path,
    *,
    allowed_root: Path,
    start_token_provider: Callable[[int], str | None] = process_start_token,
) -> list[RevenueWorkerRecord]:
    directory = _contained(registry_directory, allowed_root)
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Revenue worker registry is not a directory")
    verified: list[RevenueWorkerRecord] = []
    for path in sorted(directory.glob("*.worker.json")):
        if path.is_symlink() or path.stat().st_size > 16_384:
            continue
        try:
            record = RevenueWorkerRecord.from_document(json.loads(path.read_text(encoding="utf-8")))
            _contained(Path(record.checkpoint_path), allowed_root)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if start_token_provider(record.pid) == record.process_start_token:
            verified.append(record)
    return verified


def write_stop_receipt(path: Path, value: object) -> None:
    _atomic_json(path, value)
