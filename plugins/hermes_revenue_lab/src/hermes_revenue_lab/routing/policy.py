"""Derive and verify the single HRL-2 task-to-model policy."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

from .types import RoutingPolicy, TIER_NAMES


PAYLOAD_NAMES = (
    "model_benchmark.json",
    "model_benchmark.md",
    "model_selections.json",
)
_TIER_CONTROLS: dict[str, dict[str, object]] = {
    "no_llm": {"thinking": False, "reasoning": None, "permitted_during_luna": True},
    "fast": {"thinking": False, "reasoning": None, "permitted_during_luna": False},
    "standard": {"thinking": False, "reasoning": None, "permitted_during_luna": False},
    "reasoning": {"thinking": True, "reasoning": "low", "permitted_during_luna": False},
    "coding": {"thinking": False, "reasoning": None, "permitted_during_luna": False},
    "escalation": {"thinking": True, "reasoning": None, "permitted_during_luna": False},
}


class PolicyIntegrityError(RuntimeError):
    """Raised when policy or HRL-1 evidence cannot be authenticated."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PolicyIntegrityError(f"{name} must be an object")
    return value


def derive_policy_document(
    selections: Mapping[str, object], *, selections_sha256: str
) -> dict[str, object]:
    if selections.get("schema_version") != "hrl.model_selections.v1":
        raise PolicyIntegrityError("unsupported model selections schema")
    tiers = _mapping(selections.get("tiers"), "selection tiers")
    if set(tiers) != set(TIER_NAMES):
        raise PolicyIntegrityError("model selections must contain the exact six tiers")
    if not re.fullmatch(r"[0-9a-f]{64}", selections_sha256):
        raise PolicyIntegrityError("selection checksum is invalid")
    policy_tiers: dict[str, dict[str, object]] = {}
    for name in TIER_NAMES:
        selection = _mapping(tiers[name], f"selection tier {name}")
        status = selection.get("status")
        if status not in ("available", "unavailable"):
            raise PolicyIntegrityError(f"selection tier {name} has invalid status")
        model = selection.get("model")
        digest = selection.get("model_digest")
        if status == "available" and name != "no_llm":
            if not isinstance(model, str) or not model:
                raise PolicyIntegrityError(f"available selection tier {name} has no model")
            if not isinstance(digest, str) or not digest:
                raise PolicyIntegrityError(f"available selection tier {name} has no digest")
        elif model is not None or digest is not None:
            qualifier = "no_llm" if name == "no_llm" else f"unavailable selection tier {name}"
            raise PolicyIntegrityError(f"{qualifier} cannot contain a model")
        else:
            model = None
            digest = None
        reason = selection.get("reason")
        policy_tiers[name] = {
            "status": status,
            "model": model,
            "model_digest": digest,
            **_TIER_CONTROLS[name],
            "reason": reason if isinstance(reason, str) and reason else None,
        }
    source_fields = ("benchmark_id", "benchmark_sha256", "inventory_id")
    if any(not isinstance(selections.get(field), str) or not selections[field] for field in source_fields):
        raise PolicyIntegrityError("model selections source binding is incomplete")
    return {
        "schema_version": "hrl.model_routing_policy.v1",
        "source": {
            "benchmark_id": selections["benchmark_id"],
            "benchmark_sha256": selections["benchmark_sha256"],
            "inventory_id": selections["inventory_id"],
            "selections_sha256": selections_sha256,
        },
        "tiers": policy_tiers,
    }


def _load_json(data: bytes, name: str) -> Mapping[str, object]:
    try:
        return _mapping(json.loads(data), name)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PolicyIntegrityError(f"{name} is not valid JSON") from exc


def _manifest(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match.group(2) in entries:
            raise PolicyIntegrityError("benchmark checksum manifest is invalid")
        entries[match.group(2)] = match.group(1)
    if set(entries) != set(PAYLOAD_NAMES):
        raise PolicyIntegrityError("benchmark checksum manifest has unexpected payloads")
    return entries


def derive_verified_policy_document(
    benchmark_path: Path,
    selections_path: Path,
    checksums_path: Path,
) -> dict[str, object]:
    paths = {
        "model_benchmark.json": benchmark_path,
        "model_benchmark.md": benchmark_path.with_name("model_benchmark.md"),
        "model_selections.json": selections_path,
    }
    try:
        manifest = _manifest(checksums_path.read_text(encoding="utf-8"))
        payloads = {name: path.read_bytes() for name, path in paths.items()}
    except OSError as exc:
        raise PolicyIntegrityError("HRL-1 routing evidence is unavailable") from exc
    for name, data in payloads.items():
        if _sha256(data) != manifest[name]:
            raise PolicyIntegrityError(f"checksum mismatch for {name}")
    benchmark = _load_json(payloads["model_benchmark.json"], "model benchmark")
    selections = _load_json(payloads["model_selections.json"], "model selections")
    benchmark_sha = _sha256(payloads["model_benchmark.json"])
    if benchmark.get("status") != "completed":
        raise PolicyIntegrityError("model benchmark is not completed")
    if selections.get("benchmark_sha256") != benchmark_sha:
        raise PolicyIntegrityError("model selections benchmark checksum binding is invalid")
    for field in ("benchmark_id", "inventory_id"):
        if selections.get(field) != benchmark.get(field):
            raise PolicyIntegrityError(f"model selections {field} binding is invalid")
    return derive_policy_document(
        selections,
        selections_sha256=_sha256(payloads["model_selections.json"]),
    )


def load_verified_policy(
    policy_path: Path,
    benchmark_path: Path,
    selections_path: Path,
    checksums_path: Path,
) -> RoutingPolicy:
    expected = derive_verified_policy_document(
        benchmark_path,
        selections_path,
        checksums_path,
    )
    try:
        policy_data = policy_path.read_bytes()
    except OSError as exc:
        raise PolicyIntegrityError("routing policy is unavailable") from exc
    loaded = _load_json(policy_data, "routing policy")
    if loaded != expected:
        raise PolicyIntegrityError("routing policy does not match the derived policy")
    try:
        return RoutingPolicy.from_document(loaded)
    except ValueError as exc:
        raise PolicyIntegrityError(str(exc)) from exc
