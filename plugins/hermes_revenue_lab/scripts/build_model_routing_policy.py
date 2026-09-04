#!/usr/bin/env python3
"""Regenerate the deterministic HRL-2 routing policy from verified HRL-1 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from pathlib import Path

from hermes_revenue_lab.routing.policy import derive_verified_policy_document


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "model_benchmarks"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        position = 0
        while position < len(data):
            written = os.write(descriptor, data[position:])
            if written <= 0:
                raise OSError("policy write did not advance")
            position += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.chmod(0o644)
    os.replace(temporary, path)


def build_policy(
    target: Path,
    benchmark_path: Path,
    selections_path: Path,
    checksums_path: Path,
) -> str:
    document = derive_verified_policy_document(
        benchmark_path,
        selections_path,
        checksums_path,
    )
    data = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(target, data)
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "config" / "model_routing_policy.json")
    parser.add_argument(
        "--benchmark", type=Path, default=ARTIFACT_ROOT / "model_benchmark.json"
    )
    parser.add_argument(
        "--selections", type=Path, default=ARTIFACT_ROOT / "model_selections.json"
    )
    parser.add_argument(
        "--checksums",
        type=Path,
        default=ARTIFACT_ROOT / "model_benchmark_checksums.sha256",
    )
    args = parser.parse_args()
    digest = build_policy(args.output, args.benchmark, args.selections, args.checksums)
    print(f"routing_policy={args.output}")
    print(f"routing_policy_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
