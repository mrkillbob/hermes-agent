#!/usr/bin/env python3
"""Non-agent cron reconciliation wrapper for github-pr-feedback."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    configured = os.environ.get("HERMES_EXECUTABLE", "").strip()
    executable = Path(configured) if configured else None
    if (
        executable is None
        or not executable.is_absolute()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        print(
            "HERMES_EXECUTABLE must name an executable absolute path",
            file=sys.stderr,
        )
        return 127
    completed = subprocess.run(
        [str(executable), "github-pr-feedback", "scan"],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
