"""Read-only aggregate task diagnostics for the Kanban dashboard."""

from __future__ import annotations

import sqlite3
from typing import Optional


def compute_task_diagnostics(
    conn: sqlite3.Connection, task_ids: Optional[list[str]] = None, *,
    kanban_db, diagnostics, placeholders,
) -> dict[str, list[dict]]:
    """``{task_id: [diagnostic_dict, ...]}`` (tasks with none omitted) via three aggregate
    queries (tasks, events, runs) — slurps the board; paginate if profiling shows a hotspot."""
    from hermes_cli.config import load_config

    if task_ids is not None and not task_ids:
        return {}
    diag_config = diagnostics.config_from_runtime_config(load_config())
    if task_ids is not None:
        rows = conn.execute(f"SELECT * FROM tasks WHERE id IN ({placeholders(task_ids)})", tuple(task_ids)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks WHERE status != 'archived'").fetchall()
    if not rows:
        return {}
    row_ids = [r["id"] for r in rows]

    def _rows_by_task(table: str) -> dict[str, list]:
        by_task: dict[str, list] = {tid: [] for tid in row_ids}
        for row in conn.execute(
            f"SELECT * FROM {table} WHERE task_id IN ({placeholders(row_ids)}) ORDER BY id", tuple(row_ids)):
            by_task.setdefault(row["task_id"], []).append(row)
        return by_task

    events_by_task = _rows_by_task("task_events")
    runs_by_task = _rows_by_task("task_runs")
    graph_by_task = kanban_db.task_graph_contexts(conn, row_ids)
    out: dict[str, list[dict]] = {}
    for r in rows:
        tid = r["id"]
        diags = diagnostics.compute_task_diagnostics(
            r, events_by_task[tid], runs_by_task[tid], config=diag_config, graph=graph_by_task.get(tid))
        if diags:
            out[tid] = [d.to_dict() for d in diags]
    return out
