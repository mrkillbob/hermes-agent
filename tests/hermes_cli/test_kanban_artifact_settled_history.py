"""Synthetic canonical artifact disposition with retained settled history.

Tests run only against an isolated tmp_path canonical SQLite board. No live
board, external service or accepted private artifact is referenced.
"""
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_artifact_disposition as api
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows

BOARD = 'default'
TASK = 't_1234abcd'
PEER = 't_5678abcd'
ARTIFACT = b'Synthetic bounded discovery: IDLE is a permitted deliverable.'
PROVENANCE = b'Synthetic reviewer accepted a completed bounded discovery.'


@pytest.fixture
def store(tmp_path, monkeypatch):
    home = tmp_path / 'isolated-home'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_TASK'):
        monkeypatch.delenv(name, raising=False)
    db = kb.kanban_db_path(board=BOARD)
    assert db.resolve().is_relative_to(home.resolve())
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(kb.SCHEMA_SQL)
    with kb.write_txn(conn):
        conn.execute("INSERT INTO tasks(id,title,status,created_at,started_at,last_heartbeat_at,"
                     "workspace_kind,workspace_path,consecutive_failures,last_failure_error) "
                     "VALUES(?, 'Synthetic discovery', 'blocked', 1, 10, 12, 'dir', 'fixture', 3, 'retained')", (TASK,))
        conn.execute("INSERT INTO tasks(id,title,status,created_at) VALUES(?, 'Peer', 'triage', 1)", (PEER,))
        conn.execute("INSERT INTO task_runs(id,task_id,status,claim_lock,started_at,ended_at,outcome,last_heartbeat_at) "
                     "VALUES(1,?,'crashed','synthetic-lock',10,14,'crashed',12)", (TASK,))
        conn.execute("INSERT INTO task_events(id,task_id,run_id,kind,payload,created_at) "
                     "VALUES(1,?,1,'heartbeat',NULL,12)", (TASK,))
        conn.execute("INSERT INTO task_events(id,task_id,run_id,kind,payload,created_at) "
                     "VALUES(2,?,1,'crashed',NULL,14)", (TASK,))
        conn.execute("INSERT INTO task_comments(task_id,author,body,created_at) VALUES(?,'fixture','retain',1)", (TASK,))
        conn.execute("INSERT INTO task_attachments(task_id,filename,stored_path,size,created_at) "
                     "VALUES(?,'fixture.txt','fixture',1,1)", (TASK,))
    try:
        yield conn
    finally:
        conn.close()


def rows(conn, task=TASK):
    return _selected_task_rows(conn, [task])


def evidence(conn):
    revision = task_revision(BOARD, TASK, rows(conn))
    return api.ArtifactAcceptance(board=BOARD, task_id=TASK, reviewed_revision=revision,
                                  artifact_sha256=hashlib.sha256(ARTIFACT).hexdigest(),
                                  provenance_sha256=hashlib.sha256(PROVENANCE).hexdigest(),
                                  accepted_by='fixture-reviewer')


def apply(conn, accepted, **changes):
    args = dict(board=BOARD, expected_revision=accepted.reviewed_revision, evidence=accepted,
                artifact=ARTIFACT, provenance=PROVENANCE)
    args.update(changes)
    return api.archive_accepted_artifact(conn, TASK, **args)


def assert_archived(before, after, accepted):
    assert after['tasks'] == [dict(before['tasks'][0], status='archived')]
    for table in before.keys() - {'tasks', 'task_events'}:
        assert after[table] == before[table]
    assert after['task_events'][:-1] == before['task_events']
    event = after['task_events'][-1]
    assert event['kind'] == 'archived' and event['run_id'] is None
    payload = json.loads(event['payload'])
    expected = {**accepted.payload(), 'scope': 'artifact_only',
                'runtime_acceptance_claimed': False, 'experiment_execution_claimed': False,
                'workspace_retained': True, 'failure_state_preserved': True,
                'readiness_recomputed': False}
    if before['tasks'][0]['status'] == 'blocked':
        expected.update(prior_status='blocked', prior_run_count=len(before['task_runs']),
                        settled_history_preserved=True)
    assert payload == expected
    assert ARTIFACT.decode() not in event['payload'] and PROVENANCE.decode() not in event['payload']


def test_opt_in_archives_accepted_idle_and_preserves_history_and_peers(store, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('unrelated completion effect')
    for name in ('recompute_ready', '_cleanup_workspace', '_clear_failure_counter',
                 'notify_task_updated', '_fire_kanban_lifecycle_hook'):
        monkeypatch.setattr(kb, name, forbidden)
    before, peer = rows(store), rows(store, PEER)
    accepted = evidence(store)
    assert apply(store, accepted, allow_settled_history=True)
    assert_archived(before, rows(store), accepted)
    assert rows(store, PEER) == peer


def test_default_refuses_blocked_history_and_original_triage_still_works(store):
    before = rows(store)
    with pytest.raises(ValueError):
        apply(store, evidence(store))
    assert rows(store) == before
    with kb.write_txn(store):
        store.execute('DELETE FROM task_events WHERE task_id=?', (TASK,))
        store.execute('DELETE FROM task_runs WHERE task_id=?', (TASK,))
        store.execute("UPDATE tasks SET status='triage',started_at=NULL,last_heartbeat_at=NULL,"
                      "consecutive_failures=0,last_failure_error=NULL WHERE id=?", (TASK,))
    before = rows(store)
    accepted = evidence(store)
    assert apply(store, accepted)
    assert_archived(before, rows(store), accepted)


@pytest.mark.parametrize('finish', ['commit', 'rollback'])
def test_opt_in_nested_commit_or_outer_rollback(store, finish):
    before = rows(store)
    accepted = evidence(store)
    if finish == 'rollback':
        with pytest.raises(RuntimeError, match='synthetic rollback'):
            with kb.write_txn(store):
                assert apply(store, accepted, allow_nested=True, allow_settled_history=True)
                raise RuntimeError('synthetic rollback')
        assert rows(store) == before
    else:
        with kb.write_txn(store):
            assert apply(store, accepted, allow_nested=True, allow_settled_history=True)
            assert store.in_transaction
        assert_archived(before, rows(store), accepted)


def test_opt_in_event_failure_rolls_back_status_and_event(store, monkeypatch):
    before = rows(store)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(kb, '_append_event', fail)
    with pytest.raises(RuntimeError, match='synthetic audit failure'):
        apply(store, evidence(store), allow_settled_history=True)
    assert rows(store) == before


@pytest.mark.parametrize('change', ['sticky', 'privacy', 'provider', 'dependency', 'active',
                                    'unfinished', 'malformed_history', 'block_loop'])
def test_opt_in_preserves_guards(store, change):
    mutations = {
        'dependency': ('INSERT INTO task_links(parent_id,child_id) VALUES(?,?)', (PEER, TASK)),
        'active': ("UPDATE tasks SET claim_lock='active' WHERE id=?", (TASK,)),
        'unfinished': ("UPDATE task_runs SET ended_at=NULL,status='running' WHERE task_id=?", (TASK,)),
        'malformed_history': ('UPDATE task_events SET run_id=99 WHERE id=1', ()),
        'sticky': ('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,15)',
                   (TASK, 'blocked', json.dumps({}))),
        'privacy': ('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,15)',
                    (TASK, 'gave_up', json.dumps({'local_privacy_denial': True}))),
        'provider': ('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,15)',
                     (TASK, 'needs_attention', json.dumps({'failure_class': 'provider_egress_blocked'}))),
        'block_loop': ('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,15)',
                       (TASK, 'block_loop_detected', json.dumps({}))),
    }
    with kb.write_txn(store):
        sql, parameters = mutations[change]
        store.execute(sql, parameters)
    before = rows(store)
    with pytest.raises(ValueError):
        apply(store, evidence(store), allow_settled_history=True)
    assert rows(store) == before


@pytest.mark.parametrize('bad', [None, 0, 1, 'true'])
def test_opt_in_requires_exact_bool(store, bad):
    before = rows(store)
    with pytest.raises(ValueError):
        apply(store, evidence(store), allow_settled_history=bad)
    assert rows(store) == before


@pytest.mark.parametrize('change', ['kind', 'artifact', 'provenance', 'revision'])
def test_opt_in_rejects_unaccepted_or_mismatched_evidence(store, change):
    before = rows(store)
    accepted = evidence(store)
    overrides = {'allow_settled_history': True}
    if change == 'kind':
        accepted = replace(accepted, acceptance_kind='missing_input_stop')
    elif change == 'artifact':
        overrides['artifact'] = b'changed'
    elif change == 'provenance':
        overrides['provenance'] = b'changed'
    else:
        overrides['expected_revision'] = '0' * 64
    with pytest.raises(ValueError):
        apply(store, accepted, **overrides)
    assert rows(store) == before


def test_explicit_false_preserves_blocked_refusal(store):
    before = rows(store)
    with pytest.raises(ValueError):
        apply(store, evidence(store), allow_settled_history=False)
    assert rows(store) == before


def test_nested_audit_failure_rolls_back_only_its_savepoint(store, monkeypatch):
    before = rows(store)
    accepted = evidence(store)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(kb, '_append_event', fail)
    with kb.write_txn(store):
        store.execute("UPDATE tasks SET title='outer work' WHERE id=?", (PEER,))
        with pytest.raises(RuntimeError, match='synthetic audit failure'):
            apply(store, accepted, allow_nested=True, allow_settled_history=True)
        assert rows(store) == before
        assert rows(store, PEER)['tasks'][0]['title'] == 'outer work'
    assert rows(store) == before
    assert rows(store, PEER)['tasks'][0]['title'] == 'outer work'
