#!/usr/bin/env python3
"""Evaluate the canonical Revenue Lab guard without starting or stopping anything."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime

from hermes_revenue_lab.guard.collector import collect_revenue_snapshot
from hermes_revenue_lab.guard.policy import (
    PACIFIC,
    WorkloadKind,
    WorkloadSpec,
    evaluate_revenue_guard,
)


WORKLOADS: tuple[WorkloadKind, ...] = (
    "guard_check",
    "deterministic",
    "fast_model",
    "heavy_model",
    "image_video",
    "heavy_compile",
    "browser_swarm",
)


def _now() -> datetime:
    return datetime.now(PACIFIC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=WORKLOADS, required=True)
    parser.add_argument("--parameters", type=float)
    parser.add_argument("--allowed-model")
    parser.add_argument("--previous-swap-used-bytes", type=int)
    args = parser.parse_args(argv)
    workload = WorkloadSpec(args.workload, parameter_billions=args.parameters)
    snapshot = collect_revenue_snapshot(
        previous_swap_used_bytes=args.previous_swap_used_bytes,
        allowed_model=args.allowed_model,
    )
    decision = evaluate_revenue_guard(snapshot, workload, now=_now())
    print(
        json.dumps(
            {"decision": asdict(decision), "snapshot": asdict(snapshot)},
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if decision.permitted else 3


if __name__ == "__main__":
    raise SystemExit(main())
