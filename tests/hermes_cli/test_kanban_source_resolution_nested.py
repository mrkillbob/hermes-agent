"""Canonical source archival composes atomically with an owned SQLite transaction."""
import contextlib
import json
import sqlite3

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_source_resolution import archive_resolved_source_task
from hermes_cli.kanban_source_supersession import SourceSupersessionEvidence
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows
from hermes_constants import set_hermes_home_override, reset_hermes_home_override


BOARD = 'default'
TASK = 't_abcdef01'
EVIDENCE = SourceSupersessionEvidence('example/source', 1, 'a' * 40, 'source_finding_resolved')


@pytest.fixture
def case(tmp_path, monkeypatch):
    # Only seed disposable SQLite fixture rows; the operation under test always
    # uses the actual canonical API and its actual write_txn implementation.
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_TASK'):
        monkeypatch.delenv(name, raising=False)
    token = set_hermes_home_override(home)
    try:
        path = home / 'kanban.db'
        assert kb.kanban_db_path(board=BOARD).resolve() == path.resolve()
        with contextlib.closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(kb.SCHEMA_SQL)
            conn.execute("INSERT INTO tasks(id,title,status,created_at,started_at,consecutive_failures,"
                         "last_failure_error,workspace_kind,workspace_path,assignee) "
                         "VALUES(?, 'Synthetic accepted source', 'blocked', 1, 1, 2, "
                         "'retained failure', 'scratch', '/synthetic/retained', 'fixture-owner')", (TASK,))
            conn.execute("INSERT INTO task_runs(task_id,status,started_at,ended_at,outcome) "
                         "VALUES(?,'failed',1,2,'failed')", (TASK,))
            conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) "
                         "VALUES(?,'gave_up','{\"sticky\":false}',2)", (TASK,))
            conn.commit()
            yield conn, path
    finally:
        reset_hermes_home_override(token)


def rows(conn):
    return _selected_task_rows(conn, [TASK])


def archive(conn, **overrides):
    arguments = dict(board=BOARD, expected_revision=task_revision(BOARD, TASK, rows(conn)), evidence=EVIDENCE)
    return archive_resolved_source_task(conn, TASK, **(arguments | overrides))


def durable(path):
    with contextlib.closing(sqlite3.connect(path)) as observer:
        observer.row_factory = sqlite3.Row
        return rows(observer)


def assert_exact_effect(before, after):
    assert after['tasks'] == [dict(before['tasks'][0], status='archived')]
    for table in before.keys() - {'tasks', 'task_events'}:
        assert after[table] == before[table]
    assert after['task_events'][:-1] == before['task_events']
    assert len(after['task_events']) == len(before['task_events']) + 1
    event = after['task_events'][-1]
    assert event['task_id'] == TASK and event['kind'] == 'archived' and event['run_id'] is None
    assert json.loads(event['payload']) == dict(
        EVIDENCE.payload(), scope='source_only', reviewed_revision=task_revision(BOARD, TASK, before),
        prior_status='blocked', prior_run_count=len(before['task_runs']), runtime_acceptance_claimed=False,
        workspace_retained=True, failure_state_preserved=True, readiness_recomputed=False)


def test_default_nested_refusal_preserves_outer_transaction(case):
    conn, path = case
    before = rows(conn)
    with kb.write_txn(conn):
        with pytest.raises(RuntimeError, match='already inside a transaction'):
            archive(conn)
        assert conn.in_transaction and rows(conn) == before
    assert durable(path) == before


def test_nested_success_is_pending_and_outer_rollback_removes_both_effects(case):
    conn, path = case
    before = rows(conn)
    with pytest.raises(RuntimeError, match='fixture outer rollback'):
        with kb.write_txn(conn):
            assert archive(conn, allow_nested=True)
            assert conn.in_transaction
            assert_exact_effect(before, rows(conn))
            assert durable(path) == before
            raise RuntimeError('fixture outer rollback')
    assert not conn.in_transaction and rows(conn) == before and durable(path) == before


def test_nested_commit_makes_only_exact_effect_durable(case):
    conn, path = case
    before = rows(conn)
    with kb.write_txn(conn):
        assert archive(conn, allow_nested=True)
        assert_exact_effect(before, rows(conn))
        assert durable(path) == before
    assert not conn.in_transaction
    assert_exact_effect(before, durable(path))


def test_default_standalone_still_commits(case):
    conn, path = case
    before = rows(conn)
    assert archive(conn)
    assert not conn.in_transaction
    assert_exact_effect(before, durable(path))


@pytest.mark.parametrize('value', [None, 0, 1, '', 'true', [], {}])
def test_non_boolean_opt_in_refuses_without_effect(case, value):
    conn, path = case
    before = rows(conn)
    with kb.write_txn(conn):
        with pytest.raises(ValueError, match='allow_nested must be a bool'):
            archive(conn, allow_nested=value)
        assert conn.in_transaction and rows(conn) == before
    assert durable(path) == before


@pytest.mark.parametrize('guard', ['stale_revision', 'sticky', 'privacy', 'provider', 'block_loop',
                                 'explicit_hold', 'claim', 'dependency', 'unfinished', 'unknown_run',
                                 'historical_heartbeat'])
def test_nested_opt_in_preserves_all_refusal_guards(case, guard):
    conn, path = case
    revision = task_revision(BOARD, TASK, rows(conn))
    # Alter only synthetic preconditions before taking the reviewed snapshot.
    if guard in ('sticky', 'privacy', 'provider', 'block_loop', 'explicit_hold'):
        kind = {'block_loop': 'block_loop_detected', 'explicit_hold': 'blocked'}.get(guard, 'gave_up')
        payload = dict(sticky=guard == 'sticky', local_privacy_denial=guard == 'privacy',
                       failure_class='provider_egress_blocked' if guard == 'provider' else None)
        conn.execute('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,3)',
                     (TASK, kind, json.dumps(payload)))
    mutations = {
        'claim': [("UPDATE tasks SET claim_lock='synthetic-claim' WHERE id=?", (TASK,))],
        'dependency': [
            ("INSERT INTO tasks(id,title,status,created_at) VALUES('t_abcdef02','Dependency','blocked',1)", ()),
            ("INSERT INTO task_links(parent_id,child_id) VALUES('t_abcdef02',?)", (TASK,)),
        ],
        'unfinished': [('UPDATE task_runs SET ended_at=NULL WHERE task_id=?', (TASK,))],
        'unknown_run': [("UPDATE task_runs SET status='unknown' WHERE task_id=?", (TASK,))],
        'historical_heartbeat': [('UPDATE tasks SET last_heartbeat_at=1 WHERE id=?', (TASK,))],
    }
    for statement, parameters in mutations.get(guard, ()):
        conn.execute(statement, parameters)
    conn.commit()
    if guard != 'stale_revision':
        revision = task_revision(BOARD, TASK, rows(conn))
    else:
        conn.execute("INSERT INTO task_comments(task_id,author,body,created_at) VALUES(?,'fixture','Later',3)", (TASK,))
        conn.commit()
    before = rows(conn)
    with kb.write_txn(conn):
        with pytest.raises(ValueError):
            archive(conn, allow_nested=True, expected_revision=revision)
        assert conn.in_transaction and rows(conn) == before
    assert durable(path) == before


def test_nested_audit_failure_rolls_back_target_only_and_keeps_outer_change(case):
    conn, path = case
    before = rows(conn)
    conn.execute('CREATE TABLE composition_marker(value TEXT NOT NULL)')
    conn.execute("CREATE TRIGGER reject_synthetic_archive BEFORE INSERT ON task_events "
                 "WHEN NEW.task_id='t_abcdef01' AND NEW.kind='archived' "
                 "BEGIN SELECT RAISE(ABORT, 'synthetic audit insertion failure'); END")
    conn.commit()
    with kb.write_txn(conn):
        conn.execute("INSERT INTO composition_marker(value) VALUES('outer survives')")
        with pytest.raises(sqlite3.IntegrityError, match='synthetic audit insertion failure'):
            archive(conn, allow_nested=True)
        assert conn.in_transaction
        assert rows(conn) == before
        assert [row[0] for row in conn.execute('SELECT value FROM composition_marker')] == ['outer survives']
        assert durable(path) == before
    assert rows(conn) == before and durable(path) == before
    with contextlib.closing(sqlite3.connect(path)) as observer:
        assert observer.execute('SELECT value FROM composition_marker').fetchall() == [('outer survives',)]


def test_real_delegated_context_refuses_nested_mutation_without_losing_outer_change(case):
    from agent.delegation_context import (
        delegated_child_context, is_delegated_child_context, kanban_path_is_fenced)

    conn, path = case
    # Enter an additional owned synthetic denial scope; never clear an inherited
    # fence, replace the canonical permission check, or grant mutation authority.
    previous = is_delegated_child_context()
    assert previous is False
    before = rows(conn)
    conn.execute('CREATE TABLE composition_marker(value TEXT NOT NULL)')
    conn.commit()
    with kb.write_txn(conn):
        conn.execute("INSERT INTO composition_marker(value) VALUES('outer survives')")
        with delegated_child_context('synthetic-source-review-child'):
            assert is_delegated_child_context() and kanban_path_is_fenced(path)
            with pytest.raises(PermissionError, match='delegate_task child contexts cannot mutate'):
                archive(conn, allow_nested=True)
            assert conn.in_transaction and rows(conn) == before
        assert is_delegated_child_context() is previous
        assert conn.in_transaction
        assert [row[0] for row in conn.execute('SELECT value FROM composition_marker')] == ['outer survives']
    assert durable(path) == before
    with contextlib.closing(sqlite3.connect(path)) as observer:
        assert observer.execute('SELECT value FROM composition_marker').fetchall() == [('outer survives',)]
