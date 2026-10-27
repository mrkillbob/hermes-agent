"""Operator-reviewed source resolution of inactive cards with settled run history.

This records source disposition, never runtime completion. The operator verifies
that the accepted source satisfies the entire card before supplying revision-bound
public evidence. No worker, tool or HTTP surface exposes this operation.
"""
from __future__ import annotations

import json
from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_source_supersession import SourceSupersessionEvidence
from hermes_cli.kanban_task_revision import require_task_revision
from hermes_cli.kanban_transfer import _selected_task_rows

_TERMINAL_RUNS = frozenset(('done', 'completed', 'blocked', 'crashed', 'timed_out',
                           'failed', 'released', 'paused', 'reclaimed', 'cancelled'))


def _require_settled_source(rows):
    task = rows['tasks'][0]
    ownership = ('claim_lock', 'claim_expires', 'worker_pid', 'worker_started_at',
                 'current_run_id', 'last_heartbeat_at')
    contracts = ('workflow_template_id', 'current_step_key', 'block_kind')
    if (task['status'] != 'blocked' or any(task.get(key) is not None for key in ownership)
            or any(task.get(key) for key in contracts) or rows['task_links']
            or task.get('completion_contract') not in (None, 'local-only')
            or task.get('block_recurrences', 0) != 0):
        raise ValueError('source resolution requires inactive unlinked Blocked without holds/contracts')
    for run in rows['task_runs']:
        ended = run.get('ended_at')
        if run.get('status') not in _TERMINAL_RUNS or type(ended) is not int or ended <= 0:
            raise ValueError('source resolution preserves unfinished or unknown run history')
    for event in rows['task_events']:
        if event['kind'] == 'block_loop_detected':
            raise ValueError('source resolution preserves block-loop holds')
        if event['kind'] in ('blocked', 'gave_up', 'needs_attention'):
            try:
                value = json.loads(event.get('payload') or '{}')
            except (TypeError, ValueError):
                raise ValueError('source resolution refuses ambiguous hold history') from None
            if (not isinstance(value, dict) or value.get('sticky') or value.get('local_privacy_denial')
                    or value.get('failure_class') == 'provider_egress_blocked'):
                raise ValueError('source resolution preserves policy holds')


def archive_resolved_source_task(conn, task_id, *, board, expected_revision, evidence):
    """Change only status plus one audit event, preserving failures, runs and workspace.

    Dependency edges and external/workflow contracts refuse rather than releasing
    their acceptance. Historical started_at and failure fields remain untouched.
    No run closing, readiness recomputation, notifications or cleanup is invoked.
    """
    if not isinstance(evidence, SourceSupersessionEvidence):
        raise ValueError('explicit reviewed source evidence is required')
    payload = evidence.payload()
    if payload['reason_code'] != 'source_finding_resolved':
        raise ValueError('source resolution requires a fully resolved source finding')
    board = kb._require_slug(board)
    actual = kbc._main_db_file(conn)
    if actual is None or Path(actual).resolve() != kb.kanban_db_path(board=board).resolve():
        raise ValueError('source resolution connection does not match canonical board')
    with kb.write_txn(conn):
        rows = _selected_task_rows(conn, [task_id])
        require_task_revision(expected_revision, board, task_id, rows)
        _require_settled_source(rows)
        if kb._has_sticky_block(conn, task_id):
            raise ValueError('source resolution preserves explicit hold lineage')
        changed = conn.execute("UPDATE tasks SET status='archived' WHERE id=? AND status='blocked'", (task_id,))
        if changed.rowcount != 1:
            raise ValueError('source resolution task changed')
        kb._append_event(conn, task_id, 'archived', {
            **payload, 'scope': 'source_only', 'reviewed_revision': expected_revision,
            'prior_status': 'blocked', 'prior_run_count': len(rows['task_runs']),
            'runtime_acceptance_claimed': False, 'workspace_retained': True,
            'failure_state_preserved': True, 'readiness_recomputed': False,
        })
    return True
