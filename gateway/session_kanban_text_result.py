"""Park bounded text output for human acceptance, without external policy calls.

This is not completion or review acceptance. Existing contracts and privacy holds
remain for the later operator decision; no hook, tool or provider runs here.
"""
import hashlib
import json
from pathlib import Path
import time

from hermes_state_runtime import RuntimeStoreError

_OUTCOME = 'text_result_pending_review'


def settle_text_result(policy, scope, result):
    from agent.local_text_policy import restricted_policy
    from hermes_cli.turn_exit import turn_exit_code
    if not restricted_policy(policy):
        return result
    context = json.loads(policy.kanban_json)
    text = result.get('final_response')
    limit = context['execution_budget']['output_bytes']
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    if not turn_exit_code(result, kanban_worker=True):
        if not isinstance(text, str) or not text.strip():
            result = {**result, 'failed': True, 'completed': False, 'failure_reason': 'empty_local_text_result'}
        else:
            text = kb.redact_review_value(text)
            if len(text.encode('utf-8')) > limit:
                result = {**result, 'failed': True, 'completed': False, 'failure_reason': 'execution_output_budget'}
    if turn_exit_code(result, kanban_worker=True):
        return _record_failure(context, scope, result)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    receipt = dict(claim_lock=context['claim_lock'], pid=scope['pid'], session_id=scope['session_id'],
                   result_sha256=digest, text=text)
    with connect_closing(Path(context['db'])) as conn, kb.write_txn(conn):
        if _already_parked(conn, context, receipt):
            return result
        metadata = _require_claim(conn, context, scope)
        source_status = kb._retry_status_for_run(conn, context['task_id'], context['run_id'])
        conn.execute("UPDATE tasks SET status='blocked', claim_lock=NULL, claim_expires=NULL, "
                     "worker_pid=NULL, block_kind='needs_input' WHERE id=?", (context['task_id'],))
        run_id = kb._end_run(conn, context['task_id'], outcome=_OUTCOME, status='blocked',
                            summary='Bounded text output awaits explicit human acceptance.',
                            metadata={**metadata, 'result_sha256': digest})
        kb._append_event(conn, context['task_id'], _OUTCOME, receipt, run_id=run_id)
        # The canonical sticky block prevents recompute_ready from retrying text-only work.
        kb._append_event(conn, context['task_id'], 'blocked',
            {'kind': 'needs_input', 'reason': _OUTCOME, 'source_status': source_status}, run_id=run_id)
    return result


def _record_failure(context, scope, result):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    from hermes_cli.turn_exit import turn_exit_code
    from hermes_cli.kanban_provider_errors import owner_failure_fields
    with connect_closing(Path(context['db'])) as conn, kb.write_txn(conn):
        try:
            _require_claim(conn, context, scope)
        except RuntimeStoreError as exc:
            if exc.reason != 'stale_kanban_claim':
                raise
            return result
        # Final stop/budget/rejection evidence dominates a child's earlier optimistic rc=0.
        prior = _latest(conn, context, 'worker_result')
        if (prior.get('pid'), prior.get('claim_lock')) != (scope['pid'], context['claim_lock']):
            prior = {}
        reason = result.get('failure_reason')
        reason = reason if reason in {'empty_local_text_result', 'execution_output_budget', 'worker_request_budget_exhausted'} else 'local_text_result_not_successful'
        kb._append_event(conn, context['task_id'], 'worker_result',
            {**prior, 'pid':scope['pid'], 'claim_lock':context['claim_lock'],
             'exit_code':turn_exit_code(result, kanban_worker=True), 'result_reason':reason,
             **owner_failure_fields(result)}, run_id=context['run_id'])
    return result


def _latest(conn, context, kind):
    row = conn.execute('SELECT payload FROM task_events WHERE task_id=? AND run_id=? AND kind=? ORDER BY id DESC LIMIT 1',
                       (context['task_id'], context['run_id'], kind)).fetchone()
    return json.loads(row[0]) if row else {}


def _require_claim(conn, context, scope):
    row = conn.execute("SELECT t.status, t.current_run_id, t.claim_lock, t.claim_expires, "
        "r.status, r.task_id, r.claim_lock, r.claim_expires, r.ended_at, r.metadata FROM tasks t JOIN task_runs r ON r.id=? WHERE t.id=?",
        (context['run_id'], context['task_id'])).fetchone()
    now = int(time.time())
    if (row is None or tuple(row[:3]) != ('running', context['run_id'], context['claim_lock'])
            or tuple(row[4:7]) != ('running', context['task_id'], context['claim_lock'])
            or row[8] is not None or any(type(row[n]) is not int or row[n] < now for n in (3, 7))):
        raise RuntimeStoreError('stale_kanban_claim')
    metadata = json.loads(row[9] or '{}')
    if not isinstance(metadata, dict):
        raise RuntimeStoreError('invalid_kanban_claim')
    bound, admitted = _latest(conn, context, 'worker_bound'), _latest(conn, context, 'owner_admitted')
    if ((bound.get('pid'), bound.get('claim_lock')) != (scope['pid'], context['claim_lock'])
            or admitted.get('session_id') != scope['session_id']):
        raise RuntimeStoreError('stale_kanban_claim')
    return metadata


def _already_parked(conn, context, receipt):
    row = conn.execute("SELECT t.status, t.current_run_id, r.outcome, r.ended_at FROM tasks t JOIN task_runs r ON r.id=? "
                       "WHERE t.id=? AND r.task_id=t.id", (context['run_id'], context['task_id'])).fetchone()
    block = conn.execute("SELECT kind, run_id FROM task_events WHERE task_id=? AND kind IN ('blocked','unblocked') ORDER BY id DESC LIMIT 1",
                         (context['task_id'],)).fetchone()
    return (row is not None and tuple(row[:3]) == ('blocked', None, _OUTCOME) and row[3] is not None
            and block is not None and tuple(block) == ('blocked', context['run_id'])
            and _latest(conn, context, _OUTCOME) == receipt)
