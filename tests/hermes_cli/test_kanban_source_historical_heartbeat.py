"""Source disposition preserves recorded crash lineage without recovering workers."""
import copy
import json
import sqlite3

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_source_resolution import _require_settled_source, archive_resolved_source_task
from hermes_cli.kanban_source_supersession import SourceSupersessionEvidence
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows


BOARD = 'historical-source-review'


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path / 'home'))
    for key in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    clock = [1]
    monkeypatch.setattr(kb.time, 'time', lambda: clock[0])
    kb.create_board(BOARD)
    with connect_closing(board=BOARD) as conn:
        tid = kb.create_task(conn, title='Synthetic resolved source finding',
                             initial_status='todo', board=BOARD, workspace_kind='scratch')
        for start in (10, 20):
            clock[0] = start
            with kb.write_txn(conn):
                rid = kb._claim_and_open_run(conn, tid, 'todo', 'synthetic-owner', 100, start)
            assert rid is not None
            clock[0] = start + 2
            assert dispatch.heartbeat_worker(conn, tid, expected_run_id=rid)
            clock[0] = start + 5
            # Reproduce only the persisted crash writer relation. No recovery,
            # process discovery, signalling, dispatcher tick or death proof.
            with kb.write_txn(conn):
                conn.execute("UPDATE tasks SET status='todo',claim_lock=NULL,claim_expires=NULL "
                             "WHERE id=?", (tid,))
                assert kb._end_run(conn, tid, outcome='crashed', status='crashed',
                                   error='retained fixture failure') == rid
                kb._append_event(conn, tid, 'crashed', {}, run_id=rid)
        clock[0] = 30
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked',consecutive_failures=2,"
                         "last_failure_error='retained fixture failure' WHERE id=?", (tid,))
            kb._append_event(conn, tid, 'gave_up', {'sticky': False})
        clock[0] = 40
        yield conn, tid


def snapshot(conn, tid):
    return _selected_task_rows(conn, [tid])


def apply(conn, tid, *, revision=None):
    return archive_resolved_source_task(
        conn, tid, board=BOARD,
        expected_revision=revision or task_revision(BOARD, tid, snapshot(conn, tid)),
        evidence=SourceSupersessionEvidence('mrkillbob/hermes-agent', 435, 'a' * 40,
                                            'source_finding_resolved'))


def test_archive_preserves_exact_historical_heartbeat_and_all_failure_history(history, monkeypatch):
    conn, tid = history
    before = snapshot(conn, tid)
    assert before['tasks'][0]['last_heartbeat_at'] == 22
    assert [r['last_heartbeat_at'] for r in before['task_runs']] == [12, 22]

    def forbidden(*args, **kwargs):
        pytest.fail('source disposition invoked runtime or recovery effects')

    for name in ('recompute_ready', '_cleanup_workspace', '_clear_failure_counter', '_end_run',
                 'notify_task_updated', '_fire_kanban_lifecycle_hook', '_pid_alive'):
        monkeypatch.setattr(kb, name, forbidden)
    for name in ('dispatch_once', 'heartbeat_worker', 'detect_crashed_workers',
                 'enforce_max_runtime', 'detect_stale_running', 'reconcile_orphaned_running'):
        monkeypatch.setattr(dispatch, name, forbidden)
    assert apply(conn, tid)
    after = snapshot(conn, tid)
    assert after['tasks'] == [{**before['tasks'][0], 'status': 'archived'}]
    for table in before.keys() - {'tasks', 'task_events'}:
        assert after[table] == before[table]
    assert after['task_events'][:-1] == before['task_events']
    event = after['task_events'][-1]
    payload = json.loads(event['payload'])
    assert event['kind'] == 'archived' and event['run_id'] is None
    assert payload['scope'] == 'source_only' and payload['runtime_acceptance_claimed'] is False
    assert payload['failure_state_preserved'] and payload['workspace_retained']
    assert payload['prior_run_count'] == 2 and payload['readiness_recomputed'] is False
    with pytest.raises(ValueError):
        apply(conn, tid)
    assert snapshot(conn, tid) == after


@pytest.mark.parametrize('table,column,value', [
    ('tasks', 'claim_lock', 'owner'), ('tasks', 'claim_expires', 99),
    ('tasks', 'worker_pid', 123), ('tasks', 'worker_started_at', 'fingerprint'),
    ('tasks', 'current_run_id', 2), ('tasks', 'status', 'running'),
    ('tasks', 'workflow_template_id', 'workflow'), ('tasks', 'current_step_key', 'step'),
    ('tasks', 'completion_contract', 'external'), ('tasks', 'block_kind', 'needs_input'),
    ('tasks', 'block_recurrences', 1), ('tasks', 'started_at', 11),
    ('tasks', 'last_heartbeat_at', 0), ('tasks', 'last_heartbeat_at', -1),
    ('tasks', 'last_heartbeat_at', 41), ('tasks', 'last_heartbeat_at', 'unknown'),
    ('task_runs', 'last_heartbeat_at', None), ('task_runs', 'last_heartbeat_at', 21),
    ('task_runs', 'last_heartbeat_at', 26), ('task_runs', 'last_heartbeat_at', 22.5),
    ('task_runs', 'status', 'running'), ('task_runs', 'status', 'unknown'),
    ('task_runs', 'outcome', 'done'), ('task_runs', 'status', 'timed_out'),
    ('task_runs', 'started_at', 23), ('task_runs', 'started_at', 0),
    ('task_runs', 'ended_at', None), ('task_runs', 'ended_at', 21),
    ('task_runs', 'ended_at', 41), ('task_runs', 'ended_at', 'unknown'),
])
def test_current_authority_and_unqualified_run_history_refuse_unchanged(history, table, column, value):
    conn, tid = history
    with kb.write_txn(conn):
        where = 'id=?' if table == 'tasks' else 'id=(SELECT max(id) FROM task_runs WHERE task_id=?)'
        conn.execute(f'UPDATE {table} SET {column}=? WHERE {where}', (value, tid))
    before = snapshot(conn, tid)
    with pytest.raises(ValueError):
        apply(conn, tid)
    assert snapshot(conn, tid) == before


_LINEAGE_EDITS = {
    'missing_heartbeat': "DELETE FROM task_events WHERE task_id=:tid AND run_id=:rid AND kind='heartbeat'",
    'missing_close': "DELETE FROM task_events WHERE task_id=:tid AND run_id=:rid AND kind='crashed'",
    'wrong_heartbeat_run': "UPDATE task_events SET run_id=:rid-1 WHERE task_id=:tid AND run_id=:rid AND kind='heartbeat'",
    'unbound_heartbeat': "UPDATE task_events SET run_id=NULL WHERE task_id=:tid AND run_id=:rid AND kind='heartbeat'",
    'heartbeat_time': "UPDATE task_events SET created_at=23 WHERE task_id=:tid AND run_id=:rid AND kind='heartbeat'",
    'close_before_end': "UPDATE task_events SET created_at=24 WHERE task_id=:tid AND run_id=:rid AND kind='crashed'",
    'future_close': "UPDATE task_events SET created_at=41 WHERE task_id=:tid AND run_id=:rid AND kind='crashed'",
    'unbound_close': "UPDATE task_events SET run_id=NULL WHERE task_id=:tid AND run_id=:rid AND kind='crashed'",
    'reversed_event_time': "UPDATE task_events SET created_at=11 WHERE task_id=:tid AND run_id=:rid AND kind='crashed'",
    'no_runs': 'DELETE FROM task_runs WHERE task_id=:tid',
    'overlapping_runs': 'UPDATE task_runs SET ended_at=20 WHERE task_id=:tid AND id<>:rid',
    'old_unfinished': 'UPDATE task_runs SET ended_at=NULL WHERE task_id=:tid AND id<>:rid',
    'old_future_heartbeat': 'UPDATE task_runs SET last_heartbeat_at=41 WHERE task_id=:tid AND id<>:rid',
}


@pytest.mark.parametrize('change', _LINEAGE_EDITS)
def test_missing_or_inconsistent_lineage_refuses_unchanged(history, change):
    conn, tid = history
    latest = snapshot(conn, tid)['task_runs'][-1]['id']
    with kb.write_txn(conn):
        conn.execute(_LINEAGE_EDITS[change], {'tid': tid, 'rid': latest})
    before = snapshot(conn, tid)
    with pytest.raises(ValueError):
        apply(conn, tid)
    assert snapshot(conn, tid) == before


@pytest.mark.parametrize('kind,at', [('crashed', 40), ('cancelled', 40), ('heartbeat', 40), ('heartbeat', 22)])
def test_duplicate_close_or_late_heartbeat_refuses_unchanged(history, kind, at):
    conn, tid = history
    latest = snapshot(conn, tid)['task_runs'][-1]['id']
    with kb.write_txn(conn):
        conn.execute('INSERT INTO task_events(task_id,run_id,kind,created_at) VALUES(?,?,?,?)',
                     (tid, latest, kind, at))
    before = snapshot(conn, tid)
    with pytest.raises(ValueError):
        apply(conn, tid)
    assert snapshot(conn, tid) == before


def insert_next_heartbeat(conn, tid, latest, at):
    with kb.write_txn(conn):
        heartbeat_id = conn.execute("SELECT id FROM task_events WHERE task_id=? "
                                     "AND run_id=? AND kind='heartbeat'", (tid, latest)).fetchone()[0]
        later_ids = conn.execute('SELECT id FROM task_events WHERE id>? ORDER BY id DESC',
                                 (heartbeat_id,)).fetchall()
        for row in later_ids:
            conn.execute('UPDATE task_events SET id=? WHERE id=?', (row[0] + 1, row[0]))
        conn.execute("INSERT INTO task_events(id,task_id,run_id,kind,created_at) VALUES(?,?,?,'heartbeat',?)",
                     (heartbeat_id + 1, tid, latest, at))


@pytest.mark.parametrize('at', [22, 23])
def test_duplicate_or_later_heartbeat_refuses_with_consistent_event_order(history, at):
    conn, tid = history
    latest = snapshot(conn, tid)['task_runs'][-1]['id']
    insert_next_heartbeat(conn, tid, latest, at)
    before = snapshot(conn, tid)
    with pytest.raises(ValueError):
        apply(conn, tid)
    assert snapshot(conn, tid) == before


def test_latest_matching_heartbeat_after_earlier_progress_is_preserved(history):
    conn, tid = history
    latest = snapshot(conn, tid)['task_runs'][-1]['id']
    with kb.write_txn(conn):
        conn.execute("UPDATE task_events SET created_at=21 WHERE task_id=? AND run_id=? AND kind='heartbeat'",
                     (tid, latest))
    insert_next_heartbeat(conn, tid, latest, 22)
    before = snapshot(conn, tid)
    assert apply(conn, tid)
    after = snapshot(conn, tid)
    assert after['tasks'] == [{**before['tasks'][0], 'status': 'archived'}]
    assert after['task_runs'] == before['task_runs']
    assert after['task_events'][:-1] == before['task_events']


@pytest.mark.parametrize('status,end,heartbeat', [
    ('running', None, None), ('completed', 35, None), ('crashed', 35, None),
    ('crashed', 35, 32),
])
def test_later_run_never_reuses_prior_heartbeat_proof(history, status, end, heartbeat):
    conn, tid = history
    with kb.write_txn(conn):
        conn.execute('INSERT INTO task_runs(task_id,status,outcome,started_at,ended_at,last_heartbeat_at) '
                     'VALUES(?,?,?,31,?,?)', (tid, status, status, end, heartbeat))
    before = snapshot(conn, tid)
    with pytest.raises(ValueError):
        apply(conn, tid)
    assert snapshot(conn, tid) == before


@pytest.mark.parametrize('kind,payload', [
    ('blocked', {}), ('block_loop_detected', {}), ('gave_up', {'sticky': True}),
    ('gave_up', {'local_privacy_denial': True}),
    ('needs_attention', {'failure_class': 'provider_egress_blocked'}),
    ('needs_attention', ['ambiguous']),
])
def test_qualified_heartbeat_never_releases_holds(history, kind, payload):
    conn, tid = history
    with kb.write_txn(conn):
        kb._append_event(conn, tid, kind, payload)
    before = snapshot(conn, tid)
    with pytest.raises(ValueError):
        apply(conn, tid)
    assert snapshot(conn, tid) == before


@pytest.mark.parametrize('table,column,value', [
    ('tasks', 'last_heartbeat_at', 21), ('task_runs', 'error', 'changed prior error'),
    ('task_events', 'payload', '{"changed":true}'),
])
def test_revision_guard_binds_all_retained_history(history, table, column, value):
    conn, tid = history
    revision = task_revision(BOARD, tid, snapshot(conn, tid))
    with kb.write_txn(conn):
        where = 'id=?' if table == 'tasks' else 'task_id=?'
        conn.execute(f'UPDATE {table} SET {column}=? WHERE {where}', (value, tid))
    before = snapshot(conn, tid)
    with pytest.raises(ValueError, match='reviewed task revision changed'):
        apply(conn, tid, revision=revision)
    assert snapshot(conn, tid) == before


def test_audit_insert_failure_rolls_back_status_and_preserves_history(history):
    conn, tid = history
    conn.execute("CREATE TEMP TRIGGER reject_source_audit BEFORE INSERT ON task_events "
                 "WHEN NEW.kind='archived' BEGIN SELECT RAISE(ABORT,'audit fault'); END")
    before = snapshot(conn, tid)
    with pytest.raises(sqlite3.IntegrityError, match='audit fault'):
        apply(conn, tid)
    assert snapshot(conn, tid) == before


def test_event_order_disambiguates_heartbeat_and_close_in_same_second(history):
    conn, tid = history
    with kb.write_txn(conn):
        conn.execute('UPDATE task_runs SET ended_at=22 WHERE task_id=? AND started_at=20', (tid,))
        conn.execute("UPDATE task_events SET created_at=22 WHERE task_id=? AND run_id="
                     "(SELECT max(id) FROM task_runs WHERE task_id=?) AND kind='crashed'", (tid, tid))
    before = snapshot(conn, tid)
    assert apply(conn, tid)
    after = snapshot(conn, tid)
    assert after['tasks'] == [{**before['tasks'][0], 'status': 'archived'}]
    assert after['task_runs'] == before['task_runs']
    assert after['task_events'][:-1] == before['task_events']


def test_dependency_refuses_even_with_qualified_heartbeat(history):
    conn, tid = history
    other = kb.create_task(conn, title='Synthetic dependency', board=BOARD)
    kb.link_tasks(conn, other, tid)
    before = snapshot(conn, tid)
    with pytest.raises(ValueError):
        apply(conn, tid)
    assert snapshot(conn, tid) == before


@pytest.mark.parametrize('table,column,value', [
    ('tasks', 'last_heartbeat_at', True), ('tasks', 'started_at', True),
    ('task_runs', 'id', True), ('task_runs', 'started_at', True),
    ('task_runs', 'ended_at', True), ('task_events', 'id', True),
    ('task_events', 'created_at', True), ('task_events', 'created_at', 'unknown'),
    ('task_events', 'run_id', True), ('task_events', 'run_id', 999),
    ('task_events', 'kind', None),
])
def test_malformed_record_types_fail_closed_without_snapshot_mutation(history, table, column, value):
    conn, tid = history
    rows = snapshot(conn, tid)
    rows[table][-1][column] = value
    before = copy.deepcopy(rows)
    with pytest.raises(ValueError):
        _require_settled_source(rows)
    assert rows == before


@pytest.mark.parametrize('table', ['task_runs', 'task_events'])
def test_excessive_lineage_refuses_without_archival(history, table):
    conn, tid = history
    rows = snapshot(conn, tid)
    rows[table] = rows[table] * 4097
    before = snapshot(conn, tid)
    with pytest.raises(ValueError, match='bounded historical'):
        _require_settled_source(rows)
    assert snapshot(conn, tid) == before
