"""Read-only, bounded process ancestry and Windows job-tree correlation."""
from __future__ import annotations

import os
from pathlib import Path
import sys

import psutil

from hermes_platform.host.process_diagnostics import _image
from hermes_platform.host.windows_job import current_job_membership


def _ancestry(pid: int, root: Path, process_factory) -> dict[str, object]:
    processes = []
    seen = set()
    child_time = float("inf")
    for _ in range(8):
        if pid <= 0 or pid in seen:
            return {"processes": processes, "stop": "cycle" if pid in seen else "root"}
        seen.add(pid)
        try:
            process = process_factory(pid)
            created = process.create_time()
            if created > child_time:
                return {"processes": processes, "stop": "newer_parent"}
            parent = process.ppid()
            image = process.exe()
        except (psutil.Error, OSError):
            return {"processes": processes, "stop": "unavailable"}
        processes.append({"pid": pid, "parent_pid": parent, "create_time": created,
                          "image": _image(image, root) if image else {"unavailable": True}})
        pid, child_time = parent, created
    return {"processes": processes, "stop": "depth_limit"}


def process_job_ancestry(root: Path) -> dict[str, object] | None:
    """Observe this process and up to seven ancestors, never a process-table scan.

    Start times reject a parent PID reused after its child was born. Membership is a sampled
    PID observation, not an ownership assertion or permission to weaken a refused launch.
    No arguments, environment, raw paths, job names or handles are emitted.
    """
    if sys.platform != "win32":
        return None
    report = _ancestry(os.getpid(), root, psutil.Process)
    return _with_membership(report, current_job_membership)


def _with_membership(report: dict[str, object], query) -> dict[str, object]:
    try:
        report["job"] = query([row["pid"] for row in report["processes"]])
    except Exception:  # health: allow BLE001 -- Preserve independent ancestry evidence when a best-effort native query fails; never log exception text.
        report["job"] = {"scope": "immediate_job_tree", "unavailable": True}
    return report
