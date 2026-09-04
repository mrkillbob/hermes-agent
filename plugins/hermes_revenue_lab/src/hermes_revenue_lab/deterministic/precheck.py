"""Stateful zero-LLM prechecks that render Hermes' installed wake gate contract."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from hermes_revenue_lab.inventory.redaction import assert_publication_safe

from .catalog import require_no_llm
from .operations import hash_file, threshold_compare


_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class PrecheckDecision:
    wake_agent: bool
    context: Mapping[str, object]

    def render(self) -> str:
        if not self.wake_agent:
            return '{"wakeAgent":false}'
        if not self.context:
            raise ValueError("a waking precheck requires bounded context")
        assert_publication_safe(dict(self.context))
        return json.dumps(
            {"context": dict(self.context), "wakeAgent": True},
            sort_keys=True,
            separators=(",", ":"),
        )


def _contained(value: object, allowed_root: Path, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a path string")
    root = allowed_root.resolve(strict=True)
    candidate = Path(value)
    resolved = (
        (root / candidate).resolve(strict=False)
        if not candidate.is_absolute()
        else candidate.resolve(strict=False)
    )
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{name} is outside the allowed root") from exc
    return resolved


def _read_previous_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 128:
        raise ValueError("precheck state is not a bounded regular file")
    value = path.read_text(encoding="utf-8").strip()
    if not _SHA256.fullmatch(value):
        raise ValueError("precheck state checksum is invalid")
    return value


def _atomic_state(path: Path, digest: str) -> None:
    if path.is_symlink():
        raise ValueError("precheck state cannot be a symlink")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = f"{digest}\n".encode("ascii")
        position = 0
        while position < len(data):
            written = os.write(descriptor, data[position:])
            if written <= 0:
                raise OSError("precheck state write did not advance")
            position += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    path.chmod(0o600)


def _file_digest_gate(
    config: Mapping[str, object],
    *,
    allowed_root: Path,
) -> PrecheckDecision:
    expected = {"schema_version", "operation", "input_path", "state_path", "max_bytes"}
    if set(config) != expected:
        raise ValueError("file-digest precheck fields do not match the schema")
    max_bytes = config["max_bytes"]
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise ValueError("max_bytes must be an integer")
    input_path = _contained(config["input_path"], allowed_root, "input path")
    state_path = _contained(config["state_path"], allowed_root, "state path")
    digest = hash_file(input_path, allowed_root=allowed_root, max_bytes=max_bytes)
    previous = _read_previous_digest(state_path)
    if previous == digest:
        return PrecheckDecision(False, {})
    _atomic_state(state_path, digest)
    return PrecheckDecision(
        True,
        {"reason_code": "content_changed", "sha256": digest},
    )


def _threshold_gate(config: Mapping[str, object]) -> PrecheckDecision:
    expected = {"schema_version", "operation", "value", "operator", "threshold"}
    if set(config) != expected:
        raise ValueError("threshold precheck fields do not match the schema")
    value = config["value"]
    operator = config["operator"]
    threshold = config["threshold"]
    if not all(
        isinstance(item, (str, int)) and not isinstance(item, bool)
        for item in (value, threshold)
    ):
        raise ValueError("threshold values must be decimal strings or integers")
    if not isinstance(operator, str):
        raise ValueError("threshold operator must be a string")
    if not threshold_compare(value, operator, threshold):
        return PrecheckDecision(False, {})
    return PrecheckDecision(
        True,
        {
            "operator": operator,
            "reason_code": "threshold_met",
            "threshold": str(threshold),
            "value": str(value),
        },
    )


def evaluate_precheck(
    config: Mapping[str, object],
    *,
    allowed_root: Path,
) -> PrecheckDecision:
    assert_publication_safe(dict(config))
    if config.get("schema_version") != "hrl.precheck.v1":
        raise ValueError("unsupported precheck schema")
    operation = config.get("operation")
    if not isinstance(operation, str):
        raise ValueError("precheck operation is missing")
    try:
        require_no_llm(operation, "no_llm")
    except ValueError as exc:
        raise ValueError(f"unsupported precheck operation {operation}") from exc
    if operation in {"url_change", "document_hash"}:
        return _file_digest_gate(config, allowed_root=allowed_root)
    if operation == "threshold_compare":
        return _threshold_gate(config)
    raise ValueError(f"unsupported precheck operation {operation}")
