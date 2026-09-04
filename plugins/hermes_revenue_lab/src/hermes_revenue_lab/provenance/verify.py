"""Independent checksum and schema verification for HRL-15 run artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .types import ModelUsage, RunManifest, RunVerdict, SourceRecord

_CHECKSUM_LINE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9_./-]+)")
_CORE_FILES = {
    "manifest.json",
    "inputs.json",
    "sources.json",
    "model_usage.json",
    "verdict.json",
}
_CORE_DIRECTORIES = {"outputs", "logs"}


@dataclass(frozen=True)
class RunVerification:
    valid: bool
    run_id: str | None
    reasons: tuple[str, ...]


def _load_object(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"{path.name} must contain an object")
    return value


def verify_run(run_dir: Path, *, allowed_root: Path) -> RunVerification:
    reasons: list[str] = []
    run_id: str | None = None
    root = allowed_root.resolve(strict=True)
    resolved = run_dir.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return RunVerification(
            False, None, ("run directory is outside the allowed root",)
        )
    if not resolved.is_dir() or run_dir.is_symlink():
        return RunVerification(
            False, None, ("run directory is missing or is a symlink",)
        )

    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    symlinks: set[str] = set()
    if resolved.stat().st_mode & 0o777 != 0o700:
        reasons.append("unsafe artifact mode: .")
    for current, directories, files in os.walk(resolved, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            path = current_path / name
            relative = path.relative_to(resolved).as_posix()
            if path.is_symlink():
                symlinks.add(relative)
            elif path.is_dir():
                actual_directories.add(relative)
                if path.stat().st_mode & 0o777 != 0o700:
                    reasons.append(f"unsafe artifact mode: {relative}")
            elif path.is_file():
                actual_files.add(relative)
                if path.stat().st_mode & 0o777 != 0o600:
                    reasons.append(f"unsafe artifact mode: {relative}")
    reasons.extend(f"symlink artifact: {name}" for name in sorted(symlinks))
    for name in sorted(_CORE_DIRECTORIES - actual_directories):
        reasons.append(f"missing artifact directory: {name}")
    for name in sorted(actual_directories - _CORE_DIRECTORIES):
        reasons.append(f"untracked artifact directory: {name}")
    for name in sorted(_CORE_FILES - actual_files):
        reasons.append(f"missing artifact: {name}")
    if "checksums.sha256" not in actual_files:
        reasons.append("missing artifact: checksums.sha256")
        return RunVerification(False, None, tuple(reasons))

    declared: dict[str, str] = {}
    try:
        for line in (
            (resolved / "checksums.sha256").read_text(encoding="utf-8").splitlines()
        ):
            match = _CHECKSUM_LINE.fullmatch(line)
            if (
                match is None
                or match.group(2) in declared
                or match.group(2) == "checksums.sha256"
            ):
                raise ValueError("checksum manifest syntax is invalid")
            declared[match.group(2)] = match.group(1)
    except (OSError, UnicodeError, ValueError) as exc:
        reasons.append(str(exc))
        return RunVerification(False, None, tuple(reasons))
    payload_files = actual_files - {"checksums.sha256"}
    for name in sorted(payload_files - set(declared)):
        reasons.append(f"untracked artifact: {name}")
    for name in sorted(set(declared) - payload_files):
        reasons.append(f"missing checksummed artifact: {name}")
    for name in sorted(payload_files & set(declared)):
        observed = hashlib.sha256((resolved / name).read_bytes()).hexdigest()
        if observed != declared[name]:
            reasons.append(f"checksum mismatch: {name}")

    try:
        manifest = RunManifest.from_document(_load_object(resolved / "manifest.json"))
        run_id = manifest.run_id
        inputs = _load_object(resolved / "inputs.json")
        sources_document = _load_object(resolved / "sources.json")
        usage_document = _load_object(resolved / "model_usage.json")
        verdict = RunVerdict.from_document(_load_object(resolved / "verdict.json"))
        if (
            set(inputs) != {"schema_version", "run_id", "payload"}
            or inputs.get("schema_version") != "hrl.run_inputs.v1"
        ):
            raise ValueError("run inputs schema is invalid")
        if not isinstance(inputs["payload"], Mapping):
            raise TypeError("run inputs payload must be an object")
        if set(sources_document) != {"schema_version", "run_id", "sources"} or (
            sources_document.get("schema_version") != "hrl.run_sources.v1"
        ):
            raise ValueError("run sources schema is invalid")
        if set(usage_document) != {"schema_version", "run_id", "invocations"} or (
            usage_document.get("schema_version") != "hrl.model_usage.v1"
        ):
            raise ValueError("model usage document schema is invalid")
        sources_value = sources_document["sources"]
        usage_value = usage_document["invocations"]
        if not isinstance(sources_value, list) or not isinstance(usage_value, list):
            raise TypeError("provenance collections must be arrays")
        sources = tuple(SourceRecord.from_document(item) for item in sources_value)
        usages = tuple(ModelUsage.from_document(item) for item in usage_value)
        if len({item.source_id for item in sources}) != len(sources):
            raise ValueError("source IDs must be unique")
        if len({item.usage_id for item in usages}) != len(usages):
            raise ValueError("model usage IDs must be unique")
        bound_ids = {
            inputs["run_id"],
            sources_document["run_id"],
            usage_document["run_id"],
            verdict.run_id,
        }
        if bound_ids != {manifest.run_id}:
            raise ValueError("artifact run IDs do not match")
        if any(not item.use_permitted for item in sources) and not (
            verdict.status == "blocked" and verdict.experiment_decision == "block"
        ):
            raise ValueError("source permission state requires a blocked verdict")
        if any(item.cost_status == "unknown" for item in usages) and (
            verdict.cost_status != "unknown"
        ):
            raise ValueError(
                "total cost must remain unknown when model cost is unknown"
            )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        reasons.append(f"schema validation failed: {exc}")
    return RunVerification(not reasons, run_id, tuple(dict.fromkeys(reasons)))
