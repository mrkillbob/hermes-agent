"""Private append-only JSONL persistence for bounded routing events."""

from __future__ import annotations

import json
import os
from pathlib import Path

from hermes_revenue_lab.inventory.redaction import assert_publication_safe

from .types import RoutingEvent


LAB_ROOT = Path("/Users/mikedemott/HermesRevenueLab")
DEFAULT_LEDGER_PATH = LAB_ROOT / ".hermes" / "router" / "events.jsonl"


def _contained_path(path: Path, allowed_root: Path) -> Path:
    root = allowed_root.resolve(strict=True)
    parent = path.parent.resolve(strict=False)
    try:
        parent.relative_to(root)
    except ValueError as exc:
        raise ValueError("routing ledger path is outside Revenue Lab") from exc
    if path.is_symlink():
        raise ValueError("routing ledger target cannot be a symlink")
    return parent / path.name


def _write_all(descriptor: int, data: bytes) -> None:
    position = 0
    while position < len(data):
        written = os.write(descriptor, data[position:])
        if written <= 0:
            raise OSError("routing ledger append did not advance")
        position += written


def append_routing_event(
    path: Path,
    event: RoutingEvent,
    *,
    allowed_root: Path = LAB_ROOT,
) -> None:
    record = event.canonical_record()
    assert_publication_safe(record)
    target = _contained_path(path, allowed_root)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.parent.chmod(0o700)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        _write_all(descriptor, line.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
