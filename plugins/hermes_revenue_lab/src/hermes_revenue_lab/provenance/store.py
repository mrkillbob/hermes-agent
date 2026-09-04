"""Atomic private artifact publication for HRL-15 governed runs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path, PurePosixPath

from hermes_revenue_lab.inventory.redaction import (
    PublicationSafetyError,
    assert_publication_safe,
)

from .types import ModelUsage, RunManifest, RunVerdict, SourceRecord


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"artifact value cannot encode {type(value).__name__}")


def _render_json(value: object) -> str:
    return json.dumps(_json_value(value), sort_keys=True, indent=2) + "\n"


def _artifact_name(name: object) -> PurePosixPath:
    if not isinstance(name, str) or not name or len(name) > 240:
        raise ValueError("relative artifact path is invalid")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or path.name in {".", ".."}
        or not all(character.isalnum() or character in "._-" for character in path.name)
    ):
        raise ValueError("relative artifact path is invalid")
    return path


def _contained(path: Path, root: Path, *, label: str) -> Path:
    logical_root = Path(os.path.abspath(root))
    logical_path = Path(os.path.abspath(path))
    try:
        logical_path.relative_to(logical_root)
    except ValueError as exc:
        raise ValueError(f"{label} is outside the allowed root") from exc
    current = logical_path
    while current != logical_root and current != current.parent:
        if current.is_symlink():
            raise ValueError(f"{label} cannot contain symlinks")
        current = current.parent

    root = root.resolve(strict=True)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} is outside the allowed root") from exc
    return resolved


def _write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _checksum_text(directory: Path) -> str:
    files = sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    return "".join(
        f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
        for name in files
    )


class ArtifactRunStore:
    """Write-once store for secret-free, checksum-sealed governed run evidence."""

    def __init__(self, artifact_root: Path, *, allowed_root: Path) -> None:
        self.artifact_root = _contained(
            artifact_root, allowed_root, label="artifact root"
        )
        self.allowed_root = allowed_root.resolve(strict=True)

    def write_run(
        self,
        *,
        manifest: RunManifest,
        inputs: Mapping[str, object],
        sources: Sequence[SourceRecord],
        model_usage: Sequence[ModelUsage],
        outputs: Mapping[str, object],
        logs: Mapping[str, str],
        verdict: RunVerdict,
    ) -> Path:
        if manifest.run_id != verdict.run_id:
            raise ValueError("manifest and verdict run IDs do not match")
        source_ids = [item.source_id for item in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        usage_ids = [item.usage_id for item in model_usage]
        if len(usage_ids) != len(set(usage_ids)):
            raise ValueError("model usage IDs must be unique")
        if any(not item.use_permitted for item in sources) and not (
            verdict.status == "blocked" and verdict.experiment_decision == "block"
        ):
            raise ValueError("unpermitted or unknown source requires a blocked verdict")
        if any(item.cost_status == "unknown" for item in model_usage) and (
            verdict.cost_status != "unknown"
        ):
            raise ValueError(
                "total cost must remain unknown when model cost is unknown"
            )

        output_names = {name: _artifact_name(name) for name in outputs}
        log_names = {name: _artifact_name(name) for name in logs}
        documents: tuple[object, ...] = (
            manifest.canonical_record(),
            inputs,
            [item.canonical_record() for item in sources],
            [item.canonical_record() for item in model_usage],
            outputs,
            logs,
            verdict.canonical_record(),
        )
        try:
            for document in documents:
                assert_publication_safe(document)
        except PublicationSafetyError as exc:
            raise ValueError(str(exc)) from exc

        run_root = self.artifact_root / "runs"
        run_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.artifact_root.chmod(0o700)
        run_root.chmod(0o700)
        run_dir = run_root / manifest.run_id
        if run_dir.exists() or run_dir.is_symlink():
            raise FileExistsError(f"run already exists: {manifest.run_id}")
        staging = run_root / f".{manifest.run_id}.{uuid.uuid4().hex}.tmp"
        staging.mkdir(mode=0o700)
        try:
            (staging / "outputs").mkdir(mode=0o700)
            (staging / "logs").mkdir(mode=0o700)
            _write_private(
                staging / "manifest.json", _render_json(manifest.canonical_record())
            )
            _write_private(
                staging / "inputs.json",
                _render_json(
                    {
                        "schema_version": "hrl.run_inputs.v1",
                        "run_id": manifest.run_id,
                        "payload": inputs,
                    }
                ),
            )
            _write_private(
                staging / "sources.json",
                _render_json(
                    {
                        "schema_version": "hrl.run_sources.v1",
                        "run_id": manifest.run_id,
                        "sources": [item.canonical_record() for item in sources],
                    }
                ),
            )
            _write_private(
                staging / "model_usage.json",
                _render_json(
                    {
                        "schema_version": "hrl.model_usage.v1",
                        "run_id": manifest.run_id,
                        "invocations": [
                            item.canonical_record() for item in model_usage
                        ],
                    }
                ),
            )
            for name, path in output_names.items():
                _write_private(staging / "outputs" / path, _render_json(outputs[name]))
            for name, path in log_names.items():
                text = logs[name]
                if not isinstance(text, str):
                    raise TypeError("artifact logs must be text")
                _write_private(staging / "logs" / path, text)
            _write_private(
                staging / "verdict.json", _render_json(verdict.canonical_record())
            )
            _write_private(staging / "checksums.sha256", _checksum_text(staging))
            os.rename(staging, run_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return run_dir
