#!/usr/bin/env python3
"""Serve the HRL-16 read-only dashboard on loopback."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT / "src"))

from hermes_revenue_lab.dashboard import DashboardSnapshot, dashboard_server


def _load_snapshot(path: Path | None) -> DashboardSnapshot:
    if path is None:
        return DashboardSnapshot.unavailable(
            generated_at=datetime.now(UTC).isoformat(),
            reasons=("snapshot_path_not_configured",),
        )
    root = LAB_ROOT.resolve(strict=True)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("dashboard snapshot is outside Revenue Lab") from exc
    if (
        path.is_symlink()
        or not resolved.is_file()
        or resolved.stat().st_size > 1_000_000
    ):
        raise ValueError("dashboard snapshot is not a bounded regular file")
    document = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("dashboard snapshot root must be an object")
    return DashboardSnapshot.from_document(document)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--port", type=int, default=9131)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    provider = lambda: _load_snapshot(arguments.snapshot)
    if arguments.check:
        observed = provider()
        print(
            json.dumps({"valid": True, "freshness": observed.freshness}, sort_keys=True)
        )
        return 0
    server = dashboard_server(provider, port=arguments.port)
    print(
        f"Hermes Revenue Lab dashboard: http://127.0.0.1:{server.server_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
