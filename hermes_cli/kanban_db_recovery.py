"""Owned-run pause and bounded legacy intake recovery."""
from __future__ import annotations
import sqlite3
from typing import Any, Optional
from hermes_cli import kanban_db as kb


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
            )
        elif dead.event_payload.get("failure_class") == "provider_egress_blocked":
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
                },
            )
        elif dead.terminal_provider:
            # Provider failures belong to intake/provider repair. Do not strand
            # the task in ``blocked`` where no worker can recover it.
            routed, _, _ = kb.route_worker_block_to_orchestrator(
                conn, tid,
                reason=f"terminal provider failure: {error_text}",
            )
            tripped = False if routed else dispatch._record_task_failure(
                conn, tid,
                error=error_text,
                outcome="crashed",
                force_trip=True,
                release_claim=False,
                end_run=False,
                event_payload_extra={"pid": pid, "claimer": claimer, "terminal_provider": True},
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
                )
                tripped = False if routed else dispatch._record_task_failure(
                    conn, tid,
                    error=error_text,
                    outcome="crashed",
                    failure_limit=1 if is_systemic else failure_limit,
                    release_claim=False,
                    end_run=False,
                    event_payload_extra=extra,
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
