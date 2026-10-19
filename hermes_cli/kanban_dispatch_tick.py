"""Dispatch lane selection and the locked tick, with an optional exact-card fence."""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING, Any, Optional, Sequence

if TYPE_CHECKING:
    from hermes_cli.kanban_db import DispatchResult


logger = logging.getLogger(__name__)


def normalize_task_ids(task_ids: Optional[Sequence[str]]) -> Optional[tuple[str, ...]]:
    """None means a normal tick; every explicit selection must be nonempty and exact.

    Reject strings-as-sequences, blanks and patterns before any lock or bookkeeping.
    Duplicates retain their first position; dispatch priority remains board-defined.
    """
    if task_ids is None:
        return None
    if not isinstance(task_ids, (list, tuple)) or not task_ids:
        raise ValueError("task_ids must be a nonempty list or tuple of exact task IDs")
    if any(not isinstance(tid, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", tid) is None
           for tid in task_ids):
        raise ValueError("task_ids must contain only nonempty exact task IDs, without patterns or whitespace")
    return tuple(dict.fromkeys(task_ids))


def _lane_rows(conn: sqlite3.Connection, status: str, task_ids: Optional[tuple[str, ...]]) -> list[sqlite3.Row]:
    """Unclaimed rows of one lane in dispatch order."""
    selection_sql = ""
    params = [status]
    if task_ids is not None:
        selection_sql = f" AND id IN ({','.join('?' for _ in task_ids)})"
        params.extend(task_ids)
    return conn.execute(
        "SELECT id, assignee, created_by, title, body, provider_override, model_override FROM tasks "
        "WHERE status = ? AND claim_lock IS NULL "
        "AND COALESCE(created_by, '') != 'fleet' " + selection_sql +
        " ORDER BY priority DESC, created_at ASC", params,
    ).fetchall()


def _any_spawnable_review(
    conn: sqlite3.Connection,
    review_rows: list[sqlite3.Row],
    *,
    per_profile_cap: Optional[int] = None,
    per_profile_running: Optional[dict[str, int]] = None,
) -> bool:
    """Mirror review dispatch gates before reserving ready-lane capacity.

    Unavailable profile metadata retains the historic fail-open behavior. A
    review row that :func:`dispatch._dispatch_lane_task` would refuse this tick — its
    assignee already at the per-profile cap, or respawn-guarded — cannot
    consume the reservation, so it must not withhold capacity from an
    otherwise ready task (one such row would pin ``ready_budget`` to 0).
    """
    from hermes_cli import kanban_db_dispatch as dispatch

    if not review_rows:
        return False
    profile_exists = dispatch._profile_exists_fn()
    running = per_profile_running or {}
    for row in review_rows:
        assignee = row["assignee"]
        if not assignee:
            continue
        if profile_exists is not None and not profile_exists(assignee):
            continue
        if per_profile_cap is not None and running.get(assignee, 0) >= per_profile_cap:
            continue
        if dispatch.check_respawn_guard(conn, row["id"], lane="review") is None:
            return True
    return False


def configured_dispatch_kwargs() -> dict[str, Any]:
    """``dispatch_once`` arguments from ``kanban.*``, resolved as the gateway loop does.

    Every surface that nudges the dispatcher by hand (CLI, dashboard) needs the same host
    caps, per-profile caps and ``default_assignee``; a bare ``dispatch_once`` is uncapped.
    Raises when the config cannot be read, so a caller fails closed instead of dispatching
    without its limits.
    """
    from hermes_cli import kanban_db_dispatch as dispatch
    from hermes_cli.config import load_config

    cfg = load_config()
    section = dispatch.shared_kanban_config((cfg.get("kanban", {}) if isinstance(cfg, dict) else {}) or {})
    return dict(
        default_assignee=(section.get("default_assignee") or "").strip() or None,
        max_spawn=dispatch._positive_int(section.get("max_spawn"), None),
        # Memory-derived default when unset — the same fallback the gateway applies.
        max_in_progress=dispatch.resolve_global_max_in_progress(
            dispatch._positive_int(section.get("max_in_progress"), None),
        ),
        max_in_progress_per_profile=dispatch._positive_int(section.get("max_in_progress_per_profile"), None),
        max_in_progress_by_profile=section.get("max_in_progress_by_profile"),
        max_in_progress_per_model=section.get("max_in_progress_per_model"),
        max_in_progress_by_model=section.get("max_in_progress_by_model"),
        local_model_cap=dispatch.resolve_priority_runtime_local_cap(section.get("priority_runtime_guard", {})),
    )


def _resolve_default_assignee(default_assignee: Optional[str]) -> Optional[str]:
    """``kanban.default_assignee`` when it names a real profile this home may
    claim (``kanban.dispatch_profiles`` gated, same predicate as the spawn
    gate). Otherwise ``None`` so an unassigned shared-board card is never
    written to. When the profiles module isn't importable trust the
    operator's config: the downstream check still buckets a missing profile
    as nonspawnable."""
    from hermes_cli import kanban_db_dispatch as dispatch

    name = (default_assignee or "").strip() or None
    if name:
        profile_exists = dispatch._profile_exists_fn()
        if profile_exists is not None and not profile_exists(name):
            return None
    return name


def dispatch_tick(
    conn: sqlite3.Connection,
    *,
    spawn_fn=None,
    ttl_seconds: Optional[int] = None,
    dry_run: bool = False,
    max_spawn: Optional[int] = None,
    max_in_progress: Optional[int] = None,
    failure_limit: int,
    stale_timeout_seconds: int = 0,
    board: Optional[str] = None,
    default_assignee: Optional[str] = None,
    max_in_progress_per_profile: Optional[int] = None,
    max_in_progress_per_model: Optional[int] = None,
    max_in_progress_by_model: Optional[dict] = None,
    max_in_progress_by_profile: Optional[dict] = None,
    local_model_cap: Optional[int] = None,
    reconcile_orphans: bool = True,
    task_ids: Optional[tuple[str, ...]] = None,
) -> DispatchResult:
    """One dispatcher tick: reclaim stale/crashed running tasks, promote
    todo -> ready, then atomically claim each spawnable ready/review row and
    call ``spawn_fn(task, workspace_path, board) -> Optional[int]``, recording
    the PID so later ticks catch crashes before the TTL. Cap semantics:
    :func:`dispatch._tick_spawn_budget`."""
    from agent.estop import check_paused
    from hermes_cli import kanban_db_dispatch as dispatch

    result = dispatch.DispatchResult()
    # ``hermes pause`` is documented to hold kanban dispatch; the gateway loop checks it, but the CLI,
    # the dashboard nudge and the standalone daemon all enter here. A dry run only reports.
    if not dry_run and check_paused("kanban", logger):
        result.paused = True
        return result
    # An exact-card tick never sweeps unrelated ownership or promotes new work.
    # Running, held and staged cards remain for the normal maintenance owner.
    if task_ids is None:
        dispatch._run_reclaim_phase(
            conn, result, stale_timeout_seconds=stale_timeout_seconds,
            failure_limit=failure_limit, reconcile_orphans=reconcile_orphans, board=board,
        )
    may_spawn, spawn_budget = dispatch._tick_spawn_budget(
        conn, result, max_spawn=max_spawn, max_in_progress=max_in_progress, board=board,
    )
    if not may_spawn:
        return result
    other_running_rows = dispatch.running_task_rows_other_boards(board)

    ready_rows = _lane_rows(conn, "ready", task_ids)
    # Review rows are enumerated up front so the budget split can see whether
    # review work exists at all.
    review_rows = _lane_rows(conn, "review", task_ids) if dispatch.review_dispatch_enabled() else []
    # Per-profile cap. Deferred tasks go to skipped_per_profile_capped, not
    # skipped_unassigned — "busy, retry later" differs from "needs routing".
    # Resolved BEFORE the review reservation so the reservation can see which
    # review rows the lane loop would refuse this tick.
    per_profile_cap = max_in_progress_per_profile if (
        # Per-profile concurrency cap (#21582): when set, track how many workers each assignee already has
        # in flight, and refuse to spawn when this would push that assignee past the cap. Prevents fan-out
        # workloads from melting a single profile's local model / API quota / browser pool while leaving
        # other profiles idle.
        isinstance(max_in_progress_per_profile, int)
        and max_in_progress_per_profile > 0
    ) else None
    per_profile_running: dict[str, int] = {}
    if per_profile_cap is not None:
        for prow in conn.execute(
            "SELECT assignee, COUNT(*) AS n FROM tasks "
            "WHERE status = 'running' AND assignee IS NOT NULL "
            "GROUP BY assignee"
        ):
            per_profile_running[prow["assignee"]] = int(prow["n"])
        for other_row in other_running_rows:
            assignee = other_row.get("assignee")
            if assignee:
                per_profile_running[assignee] = per_profile_running.get(assignee, 0) + 1
    from hermes_cli.kanban_worker_capacity import WorkerCapacity

    capacity = WorkerCapacity(
        conn, model_cap=max_in_progress_per_model,
        model_caps=max_in_progress_by_model, profile_caps=max_in_progress_by_profile,
        other_running_rows=other_running_rows, local_model_cap=local_model_cap,
    )
    # Review-lane reservation: the ready loop runs first and would otherwise
    # consume the ENTIRE shared budget, starving reviews under a sustained ready
    # backlog. When spawnable review work exists and there is any budget, hold
    # one slot back.
    ready_budget = spawn_budget
    if spawn_budget is not None and spawn_budget > 0 and _any_spawnable_review(
        conn, review_rows,
        per_profile_cap=per_profile_cap, per_profile_running=per_profile_running,
    ):
        ready_budget = max(spawn_budget - 1, 0)
    lane_kwargs: dict[str, Any] = dict(
        dry_run=dry_run, ttl_seconds=ttl_seconds, board=board,
        failure_limit=failure_limit, spawn_fn=spawn_fn,
        per_profile_cap=per_profile_cap, per_profile_running=per_profile_running,
        capacity=capacity,
        other_running_rows=other_running_rows,
    )
    default_assignee = _resolve_default_assignee(default_assignee)
    spawned = 0
    for row in ready_rows:
        if ready_budget is not None and spawned >= ready_budget:
            break
        from hermes_cli.kanban_worker_routing import (
            recover_generated_assignee,
            route_orchestrator_task,
        )
        row_assignee = recover_generated_assignee(conn, row, default_assignee, dry_run=dry_run, result=result)
        if row_assignee in {"task-orchestrator", "task-intake-router", "intake-router"}:
            row_assignee = route_orchestrator_task(
                conn, row, dry_run=dry_run, result=result, board=board,
            )
            if not row_assignee:
                continue
        if not row_assignee:
            # Honour kanban.default_assignee so an unassigned task doesn't
            # park in 'ready' forever.
            if not default_assignee or not dispatch._apply_default_assignee(
                conn, row["id"], default_assignee, dry_run=dry_run,
            ):
                result.skipped_unassigned.append(row["id"])
                continue
            row_assignee = default_assignee
            result.auto_assigned_default.append(row["id"])
        if dispatch._dispatch_lane_task(conn, row, row_assignee, result, lane="ready", **lane_kwargs):
            spawned += 1

    # A review agent (sdlc-review) approves (→ done) or requests changes
    # (→ ready/todo). Review spawns share max_spawn with ready tasks. The loop
    # checks the FULL shared ``spawn_budget`` — the reservation above caps the
    # ready lane, it grants no extra capacity here.
    for row in review_rows:
        if spawn_budget is not None and spawned >= spawn_budget:
            break
        if not row["assignee"]:
            result.skipped_unassigned.append(row["id"])
            continue
        if dispatch._dispatch_lane_task(conn, row, row["assignee"], result, lane="review", **lane_kwargs):
            spawned += 1
    return result

