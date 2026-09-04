"""Atomic, checksum-bound publication for HRL-1 benchmark evidence."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from pathlib import Path

from hermes_revenue_lab.inventory.redaction import PublicationSafetyError, assert_publication_safe


PAYLOAD_NAMES = (
    "model_benchmark.json",
    "model_benchmark.md",
    "model_selections.json",
)


def _atomic_write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.chmod(mode)
    os.replace(temporary, path)


def _json(value: Mapping[str, object]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _markdown(document: Mapping[str, object], selections: Mapping[str, object]) -> str:
    records = document.get("records", [])
    rows = records if isinstance(records, list) else []
    lines = [
        "# Hermes Revenue Lab Model Benchmark",
        "",
        f"- Benchmark ID: `{document['benchmark_id']}`",
        f"- Inventory ID: `{document['inventory_id']}`",
        f"- Corpus: `{document['corpus_version']}`",
        f"- Status: `{document['status']}`",
        f"- Result rows: `{len(rows)}`",
        "",
        "## Tier selections",
        "",
        "| Tier | Status | Model | Reason |",
        "|---|---|---|---|",
    ]
    tiers = selections.get("tiers", {})
    if isinstance(tiers, dict):
        for tier, value in sorted(tiers.items()):
            row = value if isinstance(value, dict) else {}
            lines.append(
                f"| {tier} | {row.get('status', 'unavailable')} | "
                f"{row.get('model') or 'none'} | {row.get('reason', '')} |"
            )
    lines.extend(
        [
            "",
            "## Result summary",
            "",
            "| Model | Role | Task | Status | Success | Guard |",
            "|---|---|---|---|---|---|",
        ]
    )
    for value in rows:
        row = value if isinstance(value, dict) else {}
        lines.append(
            f"| {row.get('model', '')} | {row.get('role', '')} | {row.get('task_id', '')} | "
            f"{row.get('status', '')} | {row.get('success', False)} | "
            f"{row.get('guard_state', '')} |"
        )
    lines.extend(
        [
            "",
            "Canonical result detail and unavailable measurements are in `model_benchmark.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def _checksum_text(directory: Path) -> str:
    return "".join(
        f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
        for name in PAYLOAD_NAMES
    )


def publish_model_benchmark(
    document: Mapping[str, object],
    selections: Mapping[str, object],
    artifact_root: Path,
) -> dict[str, Path]:
    benchmark_id = str(document["benchmark_id"])
    if str(selections.get("inventory_id")) != str(document.get("inventory_id")):
        raise ValueError("benchmark and selections must share an inventory identity")
    run_dir = artifact_root / "runs" / benchmark_id
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    run_dir.chmod(0o700)
    try:
        assert_publication_safe(document)
        benchmark_text = _json(document)
        bound_selections = {
            **dict(selections),
            "benchmark_id": benchmark_id,
            "benchmark_sha256": hashlib.sha256(benchmark_text.encode("utf-8")).hexdigest(),
        }
        assert_publication_safe(bound_selections)
    except (PublicationSafetyError, ValueError) as exc:
        _atomic_write(
            run_dir / "rejection.json",
            _json({"benchmark_id": benchmark_id, "status": "rejected", "reason": str(exc)}),
            0o600,
        )
        raise

    candidates = {
        "model_benchmark.json": benchmark_text,
        "model_selections.json": _json(bound_selections),
        "model_benchmark.md": _markdown(document, bound_selections),
    }
    for name, text in candidates.items():
        _atomic_write(run_dir / name, text)
    _atomic_write(run_dir / "model_benchmark_checksums.sha256", _checksum_text(run_dir))
    for name in (*PAYLOAD_NAMES, "model_benchmark_checksums.sha256"):
        _atomic_write(artifact_root / name, (run_dir / name).read_text(encoding="utf-8"))
    return {
        "benchmark_json": artifact_root / "model_benchmark.json",
        "benchmark_md": artifact_root / "model_benchmark.md",
        "selections_json": artifact_root / "model_selections.json",
        "checksums": artifact_root / "model_benchmark_checksums.sha256",
        "run_directory": run_dir,
    }
