#!/usr/bin/env python3
"""Verify one checksum-sealed HRL-15 run directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT / "src"))

from hermes_revenue_lab.provenance import verify_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--allowed-root", required=True, type=Path)
    arguments = parser.parse_args()
    result = verify_run(arguments.run_directory, allowed_root=arguments.allowed_root)
    print(
        json.dumps(
            {
                "valid": result.valid,
                "run_id": result.run_id,
                "reasons": list(result.reasons),
            },
            sort_keys=True,
        )
    )
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
