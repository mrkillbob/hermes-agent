"""Exact owner results park policy denials without parsing a user's reply text."""
import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from gateway import session_kanban
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli.kanban_db_connect import connect_closing


@pytest.fixture
def attempt(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_CRASH_GRACE_SECONDS', '0')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(dispatch, '_worker_alive', lambda *_: False)
    monkeypatch.setattr(dispatch, '_classify_worker_exit', lambda _: ('unknown', None))
    monkeypatch.setattr(session_kanban, '_TURNS_DONE', threading.Event())
    with connect_closing() as conn:
        tid = kb.create_task(conn, title='Keep this assignment', body='Original scope',
                             assignee='worker', provider_override='nous', model_override='approved-model')
        task = kb.claim_task(conn, tid, claimer=kb._claimer_id())
        dispatch._set_worker_pid(conn, tid, 900001)
        with kb.write_txn(conn):
            kb._append_event(conn, tid, 'worker_bound',
                             {'pid': os.getpid(), 'claim_lock': task.claim_lock}, run_id=task.current_run_id)
        context = {'db': str(kb.kanban_db_path()), 'task_id': tid,
                   'run_id': task.current_run_id, 'claim_lock': task.claim_lock}
        yield conn, task, context


def finish(monkeypatch, context, result):
    monkeypatch.setattr(session_kanban, '_run_task_turns', lambda *_: result)
    frame = {'policy': {'kanban_json': json.dumps(context)}}
    assert session_kanban._run_worker_turns(object(), frame, []) == result


def test_owner_policy_refusal_blocks_original_task_on_first_attempt(attempt, monkeypatch):
    conn, task, context = attempt
    reply = 'Hermes blocked this request under its local privacy policy. Reason codes: base64_payload'
    finish(monkeypatch, context, {'failed': True, 'failure_reason': 'egress_policy_blocked',
           'failure_retryable': False, 'egress_reason_codes': ['base64_payload'], 'final_response': reply})
    # Owner workers have neither the CLI's initialization banner nor its raw exception text.
    kb.worker_log_path(task.id).parent.mkdir(parents=True, exist_ok=True)
    kb.worker_log_path(task.id).write_text(reply)
    dispatch.detect_crashed_workers(conn)
    current = kb.get_task(conn, task.id)
    assert current.status == 'blocked'
    assert (current.assignee, current.body, current.model_override) == ('worker', 'Original scope', 'approved-model')
    assert current.last_failure_error == 'provider egress blocked: LLM egress blocked: base64_payload'
    assert not any(event.kind == 'routed_to_repair_profile' for event in kb.list_events(conn, task.id))


def test_current_owner_crash_does_not_reuse_old_unmarked_log_denial(attempt, monkeypatch):
    conn, task, context = attempt
    finish(monkeypatch, context, {'failed': True, 'failure_reason': 'local_error', 'final_response': 'Current crash'})
    kb.worker_log_path(task.id).parent.mkdir(parents=True, exist_ok=True)
    kb.worker_log_path(task.id).write_text('LLM egress blocked: base64_payload\nold owner reply\nCurrent crash')
    dispatch.detect_crashed_workers(conn)
    assert kb.get_task(conn, task.id).status == 'ready'
    assert 'base64_payload' not in kb.get_task(conn, task.id).last_failure_error


@pytest.mark.parametrize('reason', ['timeout', 'rate_limit'])
def test_current_owner_provider_backoff_remains_retryable(attempt, monkeypatch, reason):
    conn, task, context = attempt
    finish(monkeypatch, context, {'failed': True, 'failure_reason': reason, 'failure_retryable': True})
    dispatch.detect_crashed_workers(conn)
    assert kb.get_task(conn, task.id).status == 'ready'
    assert task.id in dispatch.detect_crashed_workers._last_rate_limited


def test_human_policy_words_without_typed_failure_do_not_park_task(attempt, monkeypatch):
    conn, task, context = attempt
    finish(monkeypatch, context, {'failed': True, 'failure_reason': 'local_error',
                                 'final_response': 'Reason codes: base64_payload; Hermes blocked this request...'})
    dispatch.detect_crashed_workers(conn)
    assert kb.get_task(conn, task.id).status == 'ready'


def policy_result():
    return {'failed': True, 'failure_reason': 'egress_policy_blocked',
            'egress_reason_codes': ['base64_payload'], 'final_response': 'Policy refusal'}


@pytest.mark.parametrize('mismatch', ['result_claim', 'bound_claim', 'result_pid'])
def test_foreign_owner_policy_result_cannot_classify_current_attempt(attempt, monkeypatch, mismatch):
    conn, task, context = attempt
    finish(monkeypatch, context, policy_result())
    kind = 'worker_bound' if mismatch == 'bound_claim' else 'worker_result'
    row = conn.execute('SELECT id,payload FROM task_events WHERE task_id=? AND kind=?', (task.id, kind)).fetchone()
    payload = json.loads(row['payload'])
    payload['pid' if mismatch == 'result_pid' else 'claim_lock'] = 900002 if mismatch == 'result_pid' else 'foreign'
    with kb.write_txn(conn):
        conn.execute('UPDATE task_events SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
    dispatch.detect_crashed_workers(conn)
    assert kb.get_task(conn, task.id).status == 'ready'


def test_old_owner_policy_result_cannot_classify_new_attempt(attempt, monkeypatch):
    conn, task, context = attempt
    finish(monkeypatch, context, policy_result())
    dispatch.detect_crashed_workers(conn)
    assert kb.unblock_task(conn, task.id)
    current = kb.claim_task(conn, task.id, claimer=kb._claimer_id())
    assert current.current_run_id != task.current_run_id
    dispatch._set_worker_pid(conn, task.id, 900001)
    dispatch.detect_crashed_workers(conn)
    assert kb.get_task(conn, task.id).status == 'ready'


def test_unknown_owner_receipt_with_live_interpreter_keeps_attempt_held(attempt, monkeypatch, tmp_path):
    conn, task, context = attempt
    finish(monkeypatch, context, policy_result())
    owner_path = tmp_path / 'owner.db'
    with sqlite3.connect(owner_path) as owner:
        owner.execute('CREATE TABLE session_admissions(target_session_id TEXT,request_id TEXT,status TEXT,outcome TEXT)')
        owner.execute("INSERT INTO session_admissions VALUES('current-session','current-request','unknown',NULL)")
    with kb.write_txn(conn):
        kb._append_event(conn, task.id, 'owner_admitted',
                         {'db': str(owner_path), 'session_id': 'current-session', 'request_id': 'current-request'},
                         run_id=task.current_run_id)
    monkeypatch.setattr(dispatch, '_worker_alive', lambda pid, *_: pid == os.getpid())
    dispatch.detect_crashed_workers(conn)
    current = kb.get_task(conn, task.id)
    assert (current.status, current.current_run_id, current.claim_lock) == ('running', task.current_run_id, task.claim_lock)


def test_owner_policy_metadata_keeps_only_public_reason_codes(attempt, monkeypatch):
    conn, task, context = attempt
    result = policy_result()
    result['egress_reason_codes'] = ['base64_payload', 'private opaque payload', {'not': 'a code'}]
    finish(monkeypatch, context, result)
    persisted = session_kanban.worker_result(context['db'], context)
    assert persisted['egress_reason_codes'] == ['base64_payload']
    dispatch.detect_crashed_workers(conn)
    assert kb.get_task(conn, task.id).last_failure_error == 'provider egress blocked: LLM egress blocked: base64_payload'
