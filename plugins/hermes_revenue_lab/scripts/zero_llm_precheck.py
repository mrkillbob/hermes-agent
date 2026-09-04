#!/usr/bin/env python3
"""Run one configured deterministic precheck and print the Hermes wake gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT / "src"))

from hermes_revenue_lab.deterministic.precheck import evaluate_precheck  # noqa: E402


def _contained(path: Path, allowed_root: Path) -> Path:
    root = allowed_root.resolve(strict=True)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("precheck config is outside the allowed root") from exc
    if resolved.is_symlink():
        raise ValueError("precheck config cannot be a symlink")
    return resolved


def _default_config(allowed_root: Path) -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME", str(allowed_root / ".hermes")))
    return hermes_home / "prechecks" / f"{Path(sys.argv[0]).stem}.json"


def _load_config(path: Path, allowed_root: Path) -> dict[str, object]:
    resolved = _contained(path, allowed_root)
    if not resolved.is_file() or resolved.stat().st_size > 65_536:
        raise ValueError("precheck config is not a bounded regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("precheck config must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--allowed-root",
        type=Path,
        default=Path(os.environ.get("HERMES_WRITE_SAFE_ROOT", str(LAB_ROOT))),
    )
    args = parser.parse_args(argv)
    config_path = args.config or _default_config(args.allowed_root)
    config = _load_config(config_path, args.allowed_root)
    decision = evaluate_precheck(config, allowed_root=args.allowed_root)
    print(decision.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
