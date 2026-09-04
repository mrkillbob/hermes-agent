#!/usr/bin/env python3
"""Evaluate one proposed platform action against the HRL compliance registry."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from hermes_revenue_lab.compliance import DecisionStatus, RegistryError, load_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("config/compliance_registry.json"),
    )
    parser.add_argument("--platform", required=True)
    parser.add_argument("--action", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        decision = load_registry(args.registry).evaluate(
            platform=args.platform,
            action=args.action,
        )
    except RegistryError as exc:
        print(
            json.dumps(
                {
                    "status": DecisionStatus.BLOCK_AND_REVIEW.value,
                    "reason": str(exc),
                    "platform": args.platform,
                    "action": args.action,
                    "registry_sha256": None,
                },
                sort_keys=True,
            )
        )
        return 4
    payload = asdict(decision)
    payload["status"] = decision.status.value
    print(json.dumps(payload, sort_keys=True))
    if decision.status is DecisionStatus.ALLOW:
        return 0
    if decision.status is DecisionStatus.BLOCK:
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
