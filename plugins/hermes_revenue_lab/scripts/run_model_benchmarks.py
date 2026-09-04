#!/usr/bin/env python3
"""Run guarded HRL-1 local-model benchmarks and publish canonical evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hermes_revenue_lab.models.benchmark import run_benchmark_suite
from hermes_revenue_lab.models.benchmark_guard import (
    collect_guard_snapshot,
    evaluate_benchmark_guard,
    release_benchmark_model,
)
from hermes_revenue_lab.models.publish import publish_model_benchmark
from hermes_revenue_lab.models.resource_metrics import measure_resource_call
from hermes_revenue_lab.models.types import ModelCandidate


LAB_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = LAB_ROOT / "artifacts" / "bootstrap" / "environment_inventory.json"
DEFAULT_ARTIFACT_ROOT = LAB_ROOT / "artifacts" / "model_benchmarks"
ROLE_CHOICES = ("fast", "standard", "reasoning", "coding", "escalation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        action="append",
        choices=ROLE_CHOICES,
        help="Benchmark one approved role; repeat to add roles. Defaults to fast and standard only.",
    )
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    roles = tuple(dict.fromkeys(args.role or ("fast", "standard")))
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))

    def decision_provider(candidate: ModelCandidate):
        snapshot = collect_guard_snapshot(allowed_model=candidate.name)
        return evaluate_benchmark_guard(snapshot, candidate.parameter_billions)

    document, selections = run_benchmark_suite(
        inventory,
        requested_roles=roles,
        decision_provider=decision_provider,
        resource_measurer=measure_resource_call,
        candidate_releaser=lambda candidate: release_benchmark_model(candidate.name),
    )
    paths = publish_model_benchmark(document, selections, args.artifact_root)
    print(f"benchmark_status={document['status']}")
    print(f"benchmark_id={document['benchmark_id']}")
    for name, path in sorted(paths.items()):
        print(f"{name}={path}")
    if document["status"] == "completed":
        return 0
    if document["status"] == "blocked":
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
