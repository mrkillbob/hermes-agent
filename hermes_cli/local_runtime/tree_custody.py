"""Durable admission fence for native trees without an OS containment handle.

A router PID is not custody of children that can outlive or escape it. Reserve the
state file before POSIX spawn, retaining uncertainty even if the owner dies before
publishing the router identity. Only a future qualified containment/recovery path
may clear this fence; a dead router or an executable-name scan cannot do so.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets


def reserve_uncontained_tree(path: Path) -> dict:
    """Durably refuse competing/replacement launches before any native child exists.

    Leave a partial record on write failure: unreadable state also blocks admission.
    No existing record is overwritten, including a legacy dead-router record.
    """
    from hermes_constants import mkdir_under_hermes_home

    mkdir_under_hermes_home(path.parent)
    intent = {"tree_custody": "unverified", "spawn_ticket": secrets.token_hex(16),
              "owner_pid": os.getpid()}
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        from hermes_cli.local_runtime.reconciliation import replace_retired_record
        replace_retired_record(path, intent)
        return intent
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(intent, stream)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return intent


def verify_spawn_intent(path: Path, intent: dict) -> None:
    """Do not publish over a changed, missing or unreadable admission record."""
    try:
        current = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("local runtime spawn custody record unavailable") from exc
    if current != intent:
        raise RuntimeError("local runtime spawn custody record changed")
