"""Apply completion contracts before durable Kanban transitions."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path


class CompletionPolicyError(ValueError):
    """A registered completion contract could not be satisfied."""


def enforce_completion_policies(*, task_id, board, assignee, summary):
    from hermes_cli.plugins import invoke_hook

    results = list(invoke_hook(
        "pre_kanban_complete", task_id=task_id, board=board,
        assignee=assignee, summary=summary,
    ))
    results.extend(_control_plane_github_feedback_results(task_id=task_id))
    _raise_on_policy_results(results)


def enforce_review_policies(*, task_id, board, assignee, summary):
    """Apply feedback contracts before handing a task to human review."""
    from hermes_cli.plugins import invoke_hook

    results = list(invoke_hook(
        "pre_kanban_review", task_id=task_id, board=board,
        assignee=assignee, summary=summary,
    ))
    results.extend(_control_plane_github_feedback_results(task_id=task_id))
    _raise_on_policy_results(results)


def _raise_on_policy_results(results):
    for result in results:
        if result is None:
            continue
        if not isinstance(result, dict) or result.get("action") not in {"block", "allow", "approve"}:
            raise CompletionPolicyError("Kanban completion policy returned an invalid decision")
        if result["action"] == "block":
            raise CompletionPolicyError(result.get("message") or "Kanban completion policy rejected this transition")


def _control_plane_github_feedback_results(*, task_id):
    """Run control-plane PR-feedback policy for dispatched workers with a task binding.

    Repair workers may run under a profile that intentionally does not enable the
    optional github-pr-feedback plugin.  Their durable dispatch receipt still
    lives in ``HERMES_CONTROL_HOME``; when that receipt binds this task, enforce
    the control-plane acknowledgement contract before allowing completion.
    """
    control_home = os.environ.get("HERMES_CONTROL_HOME", "").strip()
    worker_task = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if not control_home or not worker_task:
        return []
    ledger = Path(control_home) / "github-pr-feedback" / "ledger.sqlite3"
    if not ledger.exists():
        return []
    try:
        with closing(sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_receipts'"
            ).fetchone()
            if not table:
                return []
            bound = connection.execute(
                "SELECT 1 FROM feedback_receipts WHERE task_id = ? AND feedback_kind IN "
                "('review_comment', 'issue_comment', 'review', 'pr_repair') "
                "AND NOT (feedback_kind = 'pr_repair' AND feedback_id LIKE 'report:%') LIMIT 1",
                (task_id,),
            ).fetchone()
            if not bound:
                return []
    except (OSError, sqlite3.Error, ValueError):
        return [{"action": "block", "message": "Kanban completion policy could not verify the control-plane GitHub PR feedback binding"}]
    return [guard_control_plane_completion(task_id=task_id, ledger_path=ledger)]


def guard_control_plane_completion(*, task_id, ledger_path):
    """Enforce the durable feedback contract without importing an optional plugin.

    This is deliberately a core boundary: a worker can be running with the
    github-pr-feedback directory plugin disabled while its control-plane ledger
    still owns the completion contract.
    """
    try:
        with closing(sqlite3.connect(ledger_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            rows = connection.execute(
                "SELECT status, action_status FROM feedback_receipts WHERE task_id = ? "
                "AND feedback_kind IN ('review_comment', 'issue_comment', 'review', 'pr_repair') "
                "AND NOT (feedback_kind = 'pr_repair' AND feedback_id LIKE 'report:%')",
                (task_id,),
            ).fetchall()
            if all(status == "completed" and action in {"completed", "superseded"}
                   for status, action in rows):
                return None
        reason = "this task still has an unacknowledged feedback dispatch"
    except (OSError, sqlite3.Error, ValueError, TypeError):
        reason = "the task's durable feedback completion contract could not be verified"
    return {"action": "block", "message": (
        f"Feedback completion rejected: {reason}. Finish the authorized push and factual reply, "
        "then run the exact governed complete-feedback command; use retire-feedback only for its "
        "verified closed-PR case. If the contract cannot be completed, use kanban_block with the "
        "actual blocker. A summary or local commit is not an acknowledgement."
    )}
