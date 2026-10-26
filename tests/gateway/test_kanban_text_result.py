"""Text-only execution parks a fenced result, never acceptance or automatic retry."""
from contextlib import closing
import json
from types import SimpleNamespace

import pytest


def fixture_claim(tmp_path, monkeypatch, phase='ready'):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_DELEGATED_CHILD', raising=False)
    kb.create_board('owned')
    with closing(connect(board='owned')) as conn:
        tid = kb.create_task(conn, title='Public text control', assignee='default')
        if phase == 'review':
            with kb.write_txn(conn):
                conn.execute("UPDATE tasks SET status='review' WHERE id=?", (tid,))
        task = kb.claim_review_task(conn, tid) if phase == 'review' else kb.claim_task(conn, tid)
        with kb.write_txn(conn):
            kb._append_event(conn, tid, 'worker_bound', {'pid':123, 'claim_lock':task.claim_lock}, run_id=task.current_run_id)
            kb._append_event(conn, tid, 'owner_admitted', {'session_id':'owned-session'}, run_id=task.current_run_id)
    context = dict(db=str(kb.kanban_db_path(board='owned')), task_id=tid, run_id=task.current_run_id,
        claim_lock=task.claim_lock, goal_text='Use supplied public facts.', skills=[],
        execution_budget=dict(wall_seconds=60, output_bytes=8192, requests=1, local_text_only=True))
    policy = SimpleNamespace(kanban_json=json.dumps(context), source='kanban', safe_mode=True,
                             ignore_user_config=True, toolsets=())
    return policy, context, dict(pid=123, session_id='owned-session')


def test_text_result_sticky_nonacceptance_and_idempotent(tmp_path, monkeypatch):
    from hermes_state_runtime import RuntimeStoreError
    from gateway.session_kanban_text_result import settle_text_result
    from gateway.session_kanban import _run_task_turns
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    policy, context, scope = fixture_claim(tmp_path, monkeypatch)
    calls = []
    result = {'completed':True, 'final_response':'Public bounded answer.'}
    agent = SimpleNamespace(run_conversation=lambda prompt, **kwargs: (calls.append(prompt), result)[1])
    assert _run_task_turns(agent, {'text':'must not append tool protocol'}, [], context) == result
    assert len(calls) == 1 and 'kanban_complete' not in calls[0]
    def forbidden(*args, **kwargs): pytest.fail('settlement must not invoke hooks or accept completion')
    monkeypatch.setattr(kb, '_fire_task_hook', forbidden)
    monkeypatch.setattr(kb, 'request_review', forbidden)
    monkeypatch.setattr(kb, 'complete_task', forbidden)
    assert settle_text_result(policy, scope, result) == result
    assert settle_text_result(policy, scope, result) == result
    with closing(connect(board='owned')) as conn:
        kb.recompute_ready(conn)
        task = kb.get_task(conn, context['task_id'])
        assert task.status == 'blocked' and task.current_run_id is None and task.claim_lock is None
        assert task.completed_at is None
        run = conn.execute('SELECT outcome FROM task_runs WHERE id=?', (context['run_id'],)).fetchone()
        assert run[0] == 'text_result_pending_review'
        rows = conn.execute("SELECT payload FROM task_events WHERE kind='text_result_pending_review'").fetchall()
        assert len(rows) == 1 and json.loads(rows[0][0])['text'] == result['final_response']
        with kb.write_txn(conn):
            kb._append_event(conn, context['task_id'], 'unblocked', {}, run_id=context['run_id'])
    with pytest.raises(RuntimeStoreError, match='stale_kanban_claim'):
        settle_text_result(policy, scope, result)


@pytest.mark.parametrize('change', ['claim', 'run', 'bound_pid', 'owner', 'task_expiry', 'run_expiry',
                                  'cancelled', 'failed', 'budget', 'blank', 'overflow'])
def test_text_result_refuses_stale_or_unsuccessful(tmp_path, monkeypatch, change):
    from gateway.session_kanban_text_result import settle_text_result
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    from hermes_state_runtime import RuntimeStoreError
    policy, context, scope = fixture_claim(tmp_path, monkeypatch)
    result = {'completed':True, 'final_response':'Public bounded answer.'}
    if change == 'claim': context['claim_lock'] = 'stale'
    if change == 'run': context['run_id'] += 1
    if change == 'bound_pid': scope['pid'] += 1
    if change == 'owner': scope['session_id'] = 'other-session'
    if change in {'task_expiry', 'run_expiry'}:
        with closing(connect(board='owned')) as conn, kb.write_txn(conn):
            table, key, value = ('tasks','id',context['task_id']) if change == 'task_expiry' else ('task_runs','id',context['run_id'])
            conn.execute(f'UPDATE {table} SET claim_expires=0 WHERE {key}=?', (value,))
    if change == 'cancelled': result['interrupted'] = True
    if change == 'failed': result['failed'] = True
    if change == 'budget': result.update(interrupted=True, failure_reason='worker_request_budget_exhausted')
    if change == 'blank': result['final_response'] = ' '
    if change == 'overflow': result['final_response'] = 'x' * 8193
    policy.kanban_json = json.dumps(context)
    if change in {'claim','run','bound_pid','owner','task_expiry','run_expiry'}:
        with pytest.raises(RuntimeStoreError, match='stale_kanban_claim'): settle_text_result(policy, scope, result)
    else:
        returned = settle_text_result(policy, scope, result)
        assert returned.get('failed') or returned.get('interrupted')
    with closing(connect(board='owned')) as conn:
        assert kb.get_task(conn, context['task_id']).status == 'running'
        assert not conn.execute("SELECT 1 FROM task_events WHERE kind='text_result_pending_review'").fetchone()
        if change in {'cancelled','failed','budget','blank','overflow'}:
            row = conn.execute("SELECT payload FROM task_events WHERE kind='worker_result' ORDER BY id DESC LIMIT 1").fetchone()
            assert json.loads(row[0])['exit_code'] != 0
