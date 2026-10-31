"""Idempotency keys serialize creation within their board database."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def boards(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    for key in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_WORKSPACES_ROOT',
                'HERMES_KANBAN_ATTACHMENTS_ROOT', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.create_board('alpha', name='Alpha')
    kb.create_board('beta', name='Beta')
    # Complete schema setup before either concurrent creator starts.
    for board in ('alpha', 'beta'):
        with kbc.connect_closing(board=board):
            pass
    yield
    kb._INITIALIZED_PATHS.clear()


def test_concurrent_same_key_creates_once(boards, monkeypatch):
    barrier = threading.Barrier(2)
    original_id = kb._new_task_id

    def coordinated_id():
        result = original_id()
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(kb, '_new_task_id', coordinated_id)

    def create():
        with kbc.connect_closing(board='alpha') as conn:
            return kb.create_task(conn, title='same request', idempotency_key='shared', board='alpha')

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: create(), range(2)))
    assert ids[0] == ids[1]
    with kbc.connect_closing(board='alpha') as conn:
        rows = conn.execute("SELECT id FROM tasks WHERE idempotency_key='shared'").fetchall()
        assert [row['id'] for row in rows] == [ids[0]]
        assert conn.execute("SELECT count(*) FROM task_events WHERE task_id=? AND kind='created'", (ids[0],)).fetchone()[0] == 1


def test_key_collision_retry_and_archived_reuse(boards):
    with kbc.connect_closing(board='alpha') as conn:
        first = kb.create_task(conn, title='first', body='original', idempotency_key='shared', board='alpha')
        retry = kb.create_task(conn, title='different', body='different', idempotency_key='shared', board='alpha')
        assert retry == first
        task = conn.execute('SELECT title, body FROM tasks WHERE id=?', (first,)).fetchone()
        assert (task['title'], task['body']) == ('first', 'original')
        assert conn.execute("SELECT count(*) FROM task_events WHERE task_id=? AND kind='created'", (first,)).fetchone()[0] == 1
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (first,))
        assert kb.create_task(conn, title='new', idempotency_key='shared', board='alpha') != first


def test_key_scope_is_one_board_and_outer_rollback_allows_retry(boards):
    with kbc.connect_closing(board='alpha') as alpha:
        with pytest.raises(RuntimeError, match='abort'):
            with kb.write_txn(alpha):
                kb.create_task(alpha, title='aborted', idempotency_key='shared', board='alpha')
                raise RuntimeError('abort')
        first = kb.create_task(alpha, title='retry', idempotency_key='shared', board='alpha')
        assert kb.get_task(alpha, first).title == 'retry'
    with kbc.connect_closing(board='beta') as beta:
        other = kb.create_task(beta, title='other board', idempotency_key='shared', board='beta')
        assert kb.get_task(beta, other).title == 'other board'


def test_nested_retry_does_not_add_events_or_new_subscriptions(boards):
    with kbc.connect_closing(board='alpha') as conn:
        with kb.write_txn(conn):
            first = kb.create_task(conn, title='first', idempotency_key='shared', board='alpha')
            events = conn.execute('SELECT count(*) FROM task_events WHERE task_id=?', (first,)).fetchone()[0]
            subs = conn.execute('SELECT count(*) FROM kanban_notify_subs WHERE task_id=?', (first,)).fetchone()[0]
            assert kb.create_task(conn, title='retry', idempotency_key='shared', board='alpha') == first
            assert conn.execute('SELECT count(*) FROM task_events WHERE task_id=?', (first,)).fetchone()[0] == events
            assert conn.execute('SELECT count(*) FROM kanban_notify_subs WHERE task_id=?', (first,)).fetchone()[0] == subs


def test_stale_deferred_snapshot_fails_and_outer_retry_finds_existing(boards):
    with kbc.connect_closing(board='alpha') as stale, kbc.connect_closing(board='alpha') as fresh:
        stale.execute('BEGIN')
        stale.execute('SELECT count(*) FROM tasks').fetchone()
        existing = kb.create_task(fresh, title='winner', idempotency_key='shared', board='alpha')
        with pytest.raises(sqlite3.OperationalError) as failure:
            kb.create_task(stale, title='stale', idempotency_key='shared', board='alpha')
        assert failure.value.sqlite_errorcode & 0xff == sqlite3.SQLITE_BUSY
        stale.rollback()
        assert kb.create_task(stale, title='retry', idempotency_key='shared', board='alpha') == existing
        rows = stale.execute("SELECT id FROM tasks WHERE idempotency_key='shared'").fetchall()
        assert [row['id'] for row in rows] == [existing]
        assert stale.execute("SELECT count(*) FROM task_events WHERE task_id=? AND kind='created'", (existing,)).fetchone()[0] == 1
