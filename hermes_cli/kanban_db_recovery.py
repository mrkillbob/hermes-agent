"""Owned-run pause and bounded legacy intake recovery."""
from __future__ import annotations
import sqlite3
import time
from typing import Any, Optional
from hermes_cli import kanban_db as kb


def recovery_fence_matches(conn, task_id, expected):
    """Fence reclaimed ownership and accounting, allowing diagnostic comments."""
    if expected is None:
        return True
    last_run, phase, failures, assignee = expected
    row = conn.execute(
        "SELECT status,current_run_id,claim_lock,worker_pid,consecutive_failures,assignee,"
        "(SELECT MAX(id) FROM task_runs WHERE task_id=tasks.id) AS last_run "
        "FROM tasks WHERE id=?", (task_id,),
    ).fetchone()
    return bool(row is not None and row['status'] == phase
                and row['current_run_id'] is None and not row['claim_lock']
                and row['worker_pid'] is None and row['last_run'] == last_run
                and row['consecutive_failures'] == failures and row['assignee'] == assignee)


def _account_crashes(
    conn: sqlite3.Connection,
    crash_details: list,
    *,
    failure_limit: int | None = None,
) -> list[str]:
    """Count each crash against the breaker; returns the task ids it tripped.

    Protocol violations get a BOUNDED violation-only budget independent of
    ``consecutive_failures`` (per-task ``max_retries`` takes precedence);
    systemic same-error crashes (>= 3 identical fingerprints this tick) and
    terminal provider errors (credential revoked, model gone — a retry cannot
    heal them) trip immediately.
    """
    from hermes_cli import kanban_db_dispatch as dispatch

    auto_blocked: list[str] = []
    fp_counts: dict[str, int] = {}
    for _, _, _, dead in crash_details:
        fp = dispatch._error_fingerprint(dead.error_text)
        fp_counts[fp] = fp_counts.get(fp, 0) + 1
    for tid, pid, claimer, dead in crash_details:
        error_text = dead.error_text
        guard = {'expected_recovery': dead.recovery_fence} if dead.recovery_fence is not None else {}
        if dead.protocol_violation:
            streak = dispatch._protocol_violation_streak(conn, tid)
            trow = conn.execute("SELECT max_retries FROM tasks WHERE id = ?", (tid,)).fetchone()
            if trow is None:
                continue  # task deleted mid-loop
            task_override = kb._row_get(trow, "max_retries")
            violation_limit = max(2,
                int(task_override) if task_override is not None else dispatch._PROTOCOL_VIOLATION_FAILURE_LIMIT
            )
            if streak < violation_limit:
                # Below budget: already back at ``ready`` with the error stamped.
                # No ``_record_task_failure`` — must not consume the unified budget.
                continue
            # A worker protocol failure is a routing failure, not work being
            # blocked. Hand it back through deterministic intake so the source
            # task can be repaired or reassigned; never trip the breaker into
            # the forbidden ``blocked`` lane.
            routed, _, _ = kb.route_worker_block_to_orchestrator(
                conn, tid,
                reason=(
                    f"worker protocol violation after {streak} attempts: "
                    f"{error_text}"
                ),
                **guard,
            )
            tripped = False if routed else dispatch._record_task_failure(
                conn, tid,
                error=error_text,
                outcome="crashed",
                failure_limit=violation_limit,
                force_trip=True,
                release_claim=False,
                end_run=False,
                event_payload_extra={
                    "pid": pid,
                    "claimer": claimer,
                    "protocol_violations": streak,
                    "protocol_violation_limit": violation_limit,
                },
                **guard,
            )
        elif dead.event_payload.get("failure_class") == "provider_egress_blocked":
            from hermes_cli.kanban_provider_errors import bounded_egress_reason_codes
            # A proven local policy denial cannot be repaired by changing the
            # worker role or provider. Hold the original task for correction;
            # only retain the parser's bounded reason, never appended output.
            tripped = dispatch._record_task_failure(
                conn, tid,
                error=dead.event_payload["privacy_reason"],
                outcome="crashed",
                force_trip=True,
                release_claim=False,
                end_run=False,
                event_payload_extra={
                    "pid": pid, "claimer": claimer,
                    "failure_class": "provider_egress_blocked",
                    "local_privacy_denial": True,
                    "egress_reason_codes": bounded_egress_reason_codes(dead.event_payload.get('egress_reason_codes')),
                },
                **guard,
            )
        elif dead.terminal_provider:
            # Provider failures belong to intake/provider repair. Do not strand
            # the task in ``blocked`` where no worker can recover it.
            routed, _, _ = kb.route_worker_block_to_orchestrator(
                conn, tid,
                reason=f"terminal provider failure: {error_text}",
                **guard,
            )
            tripped = False if routed else dispatch._record_task_failure(
                conn, tid,
                error=error_text,
                outcome="crashed",
                force_trip=True,
                release_claim=False,
                end_run=False,
                event_payload_extra={"pid": pid, "claimer": claimer, "terminal_provider": True},
                **guard,
            )
        else:
            is_systemic = fp_counts.get(dispatch._error_fingerprint(error_text), 0) >= 3
            extra = {"pid": pid, "claimer": claimer}
            if is_systemic:
                # Trips at 1, below any ``failure_limit``: hold it for an operator.
                extra["sticky"] = True
            if dispatch._is_worker_strategy_failure(error_text):
                routed, _, _ = kb.route_worker_block_to_orchestrator(
                    conn, tid,
                    reason=f"worker strategy failure: {error_text}",
                    **guard,
                )
                tripped = False if routed else dispatch._record_task_failure(
                    conn, tid,
                    error=error_text,
                    outcome="crashed",
                    failure_limit=1 if is_systemic else failure_limit,
                    release_claim=False,
                    end_run=False,
                    event_payload_extra=extra,
                    **guard,
                )
            else:
                tripped = dispatch._record_task_failure(
                    conn, tid,
                    error=error_text,
                    outcome="crashed",
                    failure_limit=1 if is_systemic else failure_limit,
                    release_claim=False,
                    end_run=False,
                    event_payload_extra=extra,
                    **guard,
                )
        if tripped:
            auto_blocked.append(tid)
    return auto_blocked

def _is_machine_recoverable_pr_feedback_triage(*, title: Optional[str], idempotency_key: Optional[str], block_kind: Optional[str]) -> bool:
    """Identify legacy PR-feedback intake loops that should return to workers."""
    key = (idempotency_key or '').strip().casefold()
    if not key.startswith(kb._GITHUB_PR_FEEDBACK_IDEMPOTENCY_PREFIX):
        return False
    if key.startswith(kb._GITHUB_PR_INTENT_REVIEW_PREFIX):
        return False
    if block_kind != 'needs_input':
        return False
    return (title or '').startswith('GitHub PR feedback:')

def pause_task(conn: sqlite3.Connection, task_id: str, *, expected_run_id: Optional[int]=None, claimer: Optional[str]=None, reason: str='dispatcher shutdown') -> bool:
    """Cooperatively pause one owned running task and release its claim.

    This is deliberately a run outcome, not a new card status. The task
    returns to the lane from which its run was claimed (``ready`` or
    ``review``), so the normal dispatcher can resume it later. The CAS checks
    bind the transition to the worker's run and claim, preventing an old
    worker from pausing a successor that already reclaimed the card.
    """
    with kb.write_txn(conn):
        row = conn.execute('SELECT status, current_run_id, claim_lock FROM tasks WHERE id = ?', (task_id,)).fetchone()
        if row is None or row['status'] != 'running':
            return False
        run_id = row['current_run_id']
        if run_id is None:
            return False
        if expected_run_id is not None and int(run_id) != int(expected_run_id):
            return False
        if claimer is not None and row['claim_lock'] != claimer:
            return False
        retry_status = kb._retry_status_for_run(conn, task_id, int(run_id))
        where = "WHERE id = ? AND status = 'running' AND current_run_id = ?"
        params: tuple[Any, ...] = (retry_status, task_id, int(run_id))
        if claimer is not None:
            where += ' AND claim_lock = ?'
            params += (claimer,)
        cur = conn.execute('UPDATE tasks SET status = ?, claim_lock = NULL, claim_expires = NULL, worker_pid = NULL ' + where, params)
        if cur.rowcount != 1:
            return False
        closed_run_id = kb._end_run(conn, task_id, outcome='paused', status='paused', error=None, metadata={'reason': str(reason or 'dispatcher shutdown')})
        kb._append_event(conn, task_id, 'paused', {'reason': str(reason or 'dispatcher shutdown'), 'run_id': closed_run_id, 'retry_status': retry_status}, run_id=closed_run_id)
        return True


def route_worker_block_to_orchestrator(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    reason: Optional[str] = None,
    expected_run_id: Optional[int] = None,
    expected_recovery: Optional[tuple[Optional[int], str, int, Optional[str]]] = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Route an unresolved worker handoff through the intake router.

    ``blocked`` is not a worker workflow state: it strands work until an
    operator intervenes. A worker that cannot finish is handed to a deterministic
    repair profile. The legacy ``task-orchestrator`` name is treated as a router
    alias and never remains a worker owner; unknown scope stays in triage under
    the canonical intake-router assignee.
    """
    with kb.write_txn(conn):
        if not recovery_fence_matches(conn, task_id, expected_recovery):
            return False, None, None
        row = conn.execute(
            "SELECT status, assignee, current_run_id, title, body, idempotency_key FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None or row["status"] not in {"running", "ready"}:
            return False, None, None
        current_run_id = kb._row_get(row, "current_run_id")
        if expected_run_id is not None and int(current_run_id or -1) != int(expected_run_id):
            return False, None, None
        from hermes_cli.kanban_repair_routing import repair_profile_for_task
        specialist = repair_profile_for_task(row["title"], row["body"])
        already_router = row["assignee"] in {
            "task-orchestrator", "task-intake-router", "intake-router",
        }
        same_specialist = specialist is not None and specialist == row["assignee"]
        target_assignee = "task-intake-router" if same_specialist else (
            specialist or "task-intake-router"
        )
        # A specialist's first failed attempt must not strand the card in the
        # human-only triage column. Give the intake router one bounded chance
        # to re-read the producer-owned assignment and choose a safe recovery
        # path; only an unknown scope (or a router that still cannot classify
        # it) belongs in triage.
        if already_router and specialist is None:
            new_status, new_assignee = "triage", "task-intake-router"
        elif same_specialist:
            new_status, new_assignee = "ready", "task-intake-router"
        else:
            new_status = "ready" if kb._parents_satisfied(conn, task_id) else "todo"
            new_assignee = target_assignee
        try:
            kb._validate_pr_task_assignee_authority(
                title=row["title"], body=row["body"], idempotency_key=row["idempotency_key"],
                assignee=new_assignee, initial_status=new_status,
            )
        except ValueError:
            return False, None, None
        cur = conn.execute(
            "UPDATE tasks SET status = ?, assignee = ?, claim_lock = NULL, "
            "claim_expires = NULL, worker_pid = NULL, worker_started_at = NULL, "
            "block_kind = NULL, block_recurrences = 0 "
            "WHERE id = ? AND status IN ('running', 'ready')",
            (new_status, new_assignee, task_id),
        )
        if cur.rowcount != 1:
            return False, None, None
        run_id = kb._end_or_synthesize_run(
            conn,
            task_id,
            outcome="routed_to_repair_profile",
            status="routed",
            summary=reason,
            metadata={"from_assignee": row["assignee"], "to_assignee": new_assignee,
                      "landed_status": new_status},
            synthesize=True,
        )
        kb._append_event(
            conn,
            task_id,
            "routed_to_repair_profile",
            {
                "reason": reason,
                "from_assignee": row["assignee"],
                "to_assignee": new_assignee,
                "status": new_status,
            },
            run_id=run_id,
        )
        landed = kb.get_task(conn, task_id)
    kb.notify_task_updated(conn, task_id, ("status", "assignee"))
    return True, landed.status if landed else new_status, landed.assignee if landed else new_assignee


def check_respawn_guard(
    conn: sqlite3.Connection, task_id: str, *, lane: Optional[str] = None,
) -> Optional[str]:
    """Read the newest completed attempt (timestamp then ID), preserving lane guards.
    A later result supersedes earlier cooldown/auth evidence, even in one second.
    """
    from hermes_cli import kanban_db_dispatch as dispatch
    row = conn.execute(
        "SELECT last_failure_error, status FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None

    lane = lane or (kb._retry_status_for_run(conn, task_id) if row["status"] == "running" else row["status"])

    now = int(time.time())

    # 1. Rate-limit cooldown — see docstring for why this precedes blocker_auth.
    #    LATEST run only: a newer crash/completion supersedes the rate-limit run.
    #    An infrastructure spawn refusal (#114720) shares the cooldown: the host
    #    condition is not the card's, so it retries forever, spaced, and never
    #    reaches the breaker.
    rl_cooldown = kb._resolve_rate_limit_cooldown_seconds()
    latest_run = conn.execute(
        "SELECT outcome, ended_at, metadata FROM task_runs "
        "WHERE task_id = ? AND ended_at IS NOT NULL "
        "ORDER BY ended_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    infrastructure_failure = (
        latest_run is not None and latest_run["outcome"] == "spawn_failed"
        and kb._json_dict(latest_run["metadata"]).get("infrastructure")
    )
    if infrastructure_failure and rl_cooldown > 0:
        ended_at = kb._to_epoch(latest_run["ended_at"])
        if ended_at is not None and (now - ended_at) < rl_cooldown:
            return "infrastructure_cooldown"
    if latest_run is not None and latest_run["outcome"] == "rate_limited":
        if rl_cooldown <= 0:
            # Cooldown disabled — respawn immediately, skipping blocker_auth so
            # the stamped rate-limit text doesn't re-trap the task.
            return None
        ended_at = kb._to_epoch(latest_run["ended_at"])
        if ended_at is not None and (now - ended_at) < rl_cooldown:
            return "rate_limit_cooldown"
        # Cooldown elapsed — return early so blocker_auth doesn't catch the
        # stamped rate-limit text; this path intentionally retries forever
        # (spaced by the cooldown) until quota returns or a real run supersedes it.
        return None

    # 2. Quota / auth blocker: retrying immediately will not help.  A plain
    # crash is different: its persisted error includes the worker's last
    # captured output, which is context rather than a diagnosis and may contain
    # benign commands such as ``claude auth status`` (#117097).
    err = kb._lossy_text(row["last_failure_error"])
    latest_outcome = latest_run["outcome"] if latest_run is not None else None
    if err and latest_outcome != "crashed" and not infrastructure_failure and dispatch._RESPAWN_BLOCKER_RE.search(err):
        return "blocker_auth"

    # Review-lane spawns stop here: a recent completed run and a fresh PR URL
    # are the canonical *inputs* to a review handoff, not duplicate-work signals.
    if lane == "review":
        return None

    # 3. Completed run within guard window. Exception: an explicit re-queue
    #    AFTER that success (done→ready drag, re-promotion, unblock, reclaim) is
    #    a deliberate "run it again" — otherwise a manual done→ready would sit
    #    silently held until the window elapses.
    success = max((t for t, _ in dispatch._rows_since(
        conn, "task_runs", task_id, now - dispatch._RESPAWN_GUARD_SUCCESS_WINDOW, "AND outcome = 'completed'")), default=None)
    if success is not None and not any(dispatch._rows_since(
            conn, "task_events", task_id, success, "AND kind IN ('status', 'promoted', 'unblocked', 'reclaimed')")):
        return "recent_success"

    # 4. GitHub PR URL in a recent comment — prior worker already opened a PR.
    #    Exception: a handoff AFTER the newest PR comment (operator reassign,
    #    reviewer changes_requested, review reopen) names the profile that must
    #    now work on THAT PR — a closer or the implementer finishing it, not a
    #    duplicate implementation (#111910). A crash/reclaim is not a handoff,
    #    so the worker that opened the PR is still not re-spawned against it.
    pr_times = [t for t, c in dispatch._rows_since(conn, "task_comments", task_id, now - dispatch._RESPAWN_GUARD_PR_WINDOW,
                                          cols=", body", unreadable=now)  # duplicate-PR risk: fail closed
                if dispatch._RESPAWN_GUARD_PR_URL_RE.search(kb._lossy_text(c["body"]) or "")]
    if pr_times:
        # Strictly after: a same-second tie stays guarded (fail closed).
        if any(dispatch._is_handoff_event(e["kind"], e["payload"]) for _, e in dispatch._rows_since(
                conn, "task_events", task_id, max(pr_times),
                "AND kind IN ('unblocked', 'reclaimed', 'promoted', 'review_reopened', 'changes_requested', 'assigned')", cols=", kind, payload", strict=True)):
            return None
        return "active_pr"

    return None
