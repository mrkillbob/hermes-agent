"""Bounded, read-only accounting across pool source generations.

This module never acquires/releases leases or changes files. A review candidate
is a request for owner review, never permission to delete. Expensive Git,
process, config and board probes are supplied as explicit caller evidence;
missing evidence protects the slot. Symlink targets are reported, not traversed.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

_REPO = re.compile(r"repo-[0-9a-f]{16}\Z")
_SOURCE = re.compile(r"source-([0-9a-f]{16})\Z")
_SLOT = re.compile(r"slot-(\d+)\Z")
_TERMINAL = frozenset({"done", "archived"})


def read_ledger(path: Path) -> dict[str, Any]:
    """Read the real WAL-visible ledger; unavailable/schema errors protect all."""
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=1)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute(
                "SELECT slot_id, status, owner_pid, task_id, board, lease_version "
                "FROM worktree_pool_slots"
            )]
        return {"available": True, "rows": rows}
    except (OSError, sqlite3.Error):
        # Avoid leaking SQL, private paths or arbitrary exception text.
        return {"available": False, "rows": [], "error": "ledger_unavailable"}


def classify(lease: dict[str, Any] | None, evidence: dict[str, Any],
             *, ledger_available: bool, symlink: bool = False) -> list[str]:
    """Require positive complete evidence; free or missing leases prove nothing."""
    reasons: list[str] = []
    if symlink:
        reasons.append("symlink_not_traversed")
    if not ledger_available:
        reasons.append("ledger_unknown")
    elif lease is None:
        reasons.append("lease_record_missing")
    elif lease.get("status") != "free":
        reasons.append("leased_or_unknown_status")
    for key in ("git_valid", "clean_including_untracked", "no_unpushed_commits",
                "board_inventory_complete", "descendants_finished", "source_identity_known"):
        if evidence.get(key) is not True:
            reasons.append(key + "_not_proven")
    for key in ("config_reference", "process_reference"):
        if evidence.get(key) is not False:
            reasons.append(key + "_present_or_unknown")
    statuses = evidence.get("board_statuses")
    if not isinstance(statuses, list) or any(not isinstance(s, str) or s not in _TERMINAL for s in statuses):
        reasons.append("board_binding_active_or_unknown")
    if lease and lease.get("task_id"):
        # A terminal historical card list must also positively cover this lease.
        if evidence.get("lease_binding_resolved") is not True:
            reasons.append("lease_board_binding_unknown")
    return reasons


def _children(path: Path) -> list[Path]:
    # Fixed three-level metadata walk; no worktree contents or link traversal.
    with os.scandir(path) as entries:
        return sorted((Path(entry.path) for entry in entries), key=str)


def inventory(root: Path, ledger: dict[str, Any],
              evidence: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    evidence = evidence or {}
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    by_id = {row["slot_id"]: row for row in ledger.get("rows", [])}
    if root.is_symlink():
        return {"slots": [], "generations": [], "issues": ["pool_root_symlink"],
                "destructive_actions_supported": False}
    try:
        for repository in _children(root):
            if not _REPO.fullmatch(repository.name):
                continue
            if repository.is_symlink() or not repository.is_dir():
                issues.append("repository_not_traversed:" + repository.name)
                continue
            for source in _children(repository):
                match = _SOURCE.fullmatch(source.name)
                if not match:
                    continue
                if source.is_symlink() or not source.is_dir():
                    issues.append("source_not_traversed:" + source.name)
                    continue
                namespace = match.group(1)
                for path in _children(source):
                    slot = _SLOT.fullmatch(path.name)
                    if not slot:
                        continue
                    slot_id = int(slot.group(1))
                    ledger_id = (int(namespace, 16) % 100_000_000) * 32 + slot_id
                    proof = evidence.get(str(path), {})
                    lease = by_id.get(ledger_id)
                    reasons = classify(lease, proof,
                                       ledger_available=ledger.get("available") is True,
                                       symlink=path.is_symlink())
                    if slot_id >= 16 or (not path.is_symlink() and not path.is_dir()):
                        reasons.append("unexpected_slot_layout")
                    identity = proof.get("source_identity")
                    if not isinstance(identity, str) or sha256(identity.encode()).hexdigest()[:16] != namespace:
                        reasons.append("source_identity_hash_unresolved")
                    venv = path / ".venv"
                    # A slot symlink must not lead even a metadata probe outside root.
                    venv_target = None
                    if not path.is_symlink() and venv.is_symlink():
                        venv_target = os.readlink(venv)
                    size = proof.get("logical_bytes")
                    if type(size) is not int or size < 0:
                        size = None
                    rows.append({"path": str(path), "repository": repository.name,
                                 "source_namespace": namespace, "slot_id": slot_id,
                                 "ledger_slot_id": ledger_id,
                                 "lease_status": lease.get("status") if lease else None,
                                 "source_identity": proof.get("source_identity"),
                                 "logical_bytes": size, "venv_link_target": venv_target,
                                 "protection_reasons": reasons,
                                 "disposition": "PRESERVE" if reasons else "OWNER_REVIEW_CANDIDATE",
                                 "deletion_authorized": False})
    except OSError:
        issues.append("inventory_incomplete")
    if len({row["ledger_slot_id"] for row in rows}) != len(rows):
        issues.append("ambiguous_ledger_slot_identity")
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["repository"], row["source_namespace"])
        group = groups.setdefault(key, {"repository": key[0], "source_namespace": key[1],
                                       "slots": 0, "known_logical_bytes": 0,
                                       "unknown_size_slots": 0, "review_candidates": 0})
        group["slots"] += 1
        if row["logical_bytes"] is None:
            group["unknown_size_slots"] += 1
        else:
            group["known_logical_bytes"] += row["logical_bytes"]
        group["review_candidates"] += row["disposition"] == "OWNER_REVIEW_CANDIDATE"
    # Incomplete inventory cannot support a candidate assessment.
    if issues:
        for row in rows:
            row["protection_reasons"].append("global_inventory_incomplete")
            row["disposition"] = "PRESERVE"
        for group in groups.values():
            group["review_candidates"] = 0
    return {"slots": rows, "generations": list(groups.values()), "issues": issues,
            "destructive_actions_supported": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--evidence", type=Path,
                        help="Explicit owner-collected proof keyed by exact slot path; no probing")
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text()) if args.evidence else {}
    print(json.dumps(inventory(args.pool_root, read_ledger(args.ledger), evidence), indent=2))


if __name__ == "__main__":
    main()
