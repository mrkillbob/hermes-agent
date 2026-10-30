"""``cronjob_manage(action="runs")``: recent execution outcomes for one job.

Only the outcome, never an execution ID or error text: those carry identifiers and
free-form provider output that the LLM egress firewall refuses, and the model needs
neither to tell whether a job ran and how it ended.
"""

from __future__ import annotations

import json
import re
from typing import Any

RUNS_LIMIT = 10
RUN_STATUSES = frozenset({"claimed", "running", "completed", "failed", "unknown"})
# ``claimed_at`` / ``finished_at`` are written by cron.executions as ISO-8601 with an offset.
RUN_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?[+-]\d{2}:\d{2}")


def _stamp(value: Any) -> str | None:
    return value if isinstance(value, str) and RUN_STAMP.fullmatch(value) else None


def action_runs(job: dict[str, Any], _args: dict[str, Any]) -> str:
    from cron.executions import list_executions

    records = list_executions(job_id=job["id"], limit=RUNS_LIMIT)
    runs = []
    for record in records:
        claimed = _stamp(record.get("claimed_at"))
        if claimed is None:
            continue
        status = record.get("status")
        run = {"status": status if status in RUN_STATUSES else "unknown", "claimed_at": claimed}
        finished = _stamp(record.get("finished_at"))
        if finished:
            run["finished_at"] = finished
        runs.append(run)
    return json.dumps({"success": True, "count": len(runs), "runs": runs}, separators=(",", ":"))
