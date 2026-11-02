"""Actual temp-store commit placement, not native authentication or a receiver lease."""
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    store = SessionDB(tmp_path / 'state.db')
    try:
        yield store
    finally:
        store.close()


def saved(db, key='synthetic-guard-test'):
    with sqlite3.connect(db.db_path) as observer:
        return observer.execute('SELECT value FROM state_meta WHERE key=?', (key,)).fetchone()


def write(connection):
    connection.execute('INSERT INTO state_meta(key,value) VALUES(?,?)',
                       ('synthetic-guard-test', 'synthetic-value'))
    return 'stored'


def test_guard_enters_after_sql_and_exits_after_actual_commit(db):
    observations = []

    @contextmanager
    def guard(connection):
        assert connection.in_transaction
        assert connection.execute('SELECT value FROM state_meta WHERE key=?',
                                  ('synthetic-guard-test',)).fetchone()[0] == 'synthetic-value'
        assert saved(db) is None  # another connection cannot see uncommitted SQL
        observations.append('lease-entered')
        yield
        assert not connection.in_transaction
        assert saved(db) == ('synthetic-value',)
        observations.append('lease-released')

    assert db._execute_write(write, _commit_guard=guard) == 'stored'
    assert observations == ['lease-entered', 'lease-released']


def test_guard_refusal_rolls_back_sql_and_ordinary_writes_still_commit(db):
    @contextmanager
    def refused(connection):
        raise ValueError('synthetic-expired-lease')
        yield  # contextmanager entry refuses; no production authorizer is mocked

    with pytest.raises(ValueError, match='synthetic-expired-lease'):
        db._execute_write(write, _commit_guard=refused)
    assert saved(db) is None
    assert db._execute_write(write) == 'stored'
    assert saved(db) == ('synthetic-value',)


def test_callback_failure_does_not_enter_commit_lease(db):
    entered = []

    @contextmanager
    def guard(connection):
        entered.append(True)
        yield

    def failed(connection):
        write(connection)
        raise ValueError('synthetic-write-failure')

    with pytest.raises(ValueError, match='synthetic-write-failure'):
        db._execute_write(failed, _commit_guard=guard)
    assert entered == []
    assert saved(db) is None


@pytest.mark.parametrize('error', [ValueError('synthetic-release-failure'),
                                  sqlite3.OperationalError('database is locked')])
def test_guard_exit_failure_preserves_uncertain_committed_effect_without_retry(db, error):
    from hermes_state_commit_guard import CommitGuardFailure
    calls = []

    def counted(connection):
        calls.append(True)
        return write(connection)

    @contextmanager
    def guard(connection):
        yield
        raise error

    with pytest.raises(CommitGuardFailure, match='commit_guard_release_failed'):
        db._execute_write(counted, _commit_guard=guard)
    # Rollback after commit cannot undo this. Callers must query the SAME operation.
    assert saved(db) == ('synthetic-value',)
    assert calls == [True]


def test_guard_cannot_turn_a_suppressed_commit_failure_into_success():
    from hermes_state_commit_guard import CommitGuardFailure, commit_transaction
    def failed_commit():
        raise OSError('synthetic-commit-failure')

    @contextmanager
    def suppressing(connection):
        try:
            yield
        except OSError:
            pass

    with pytest.raises(CommitGuardFailure, match='commit_guard_suppressed_failure'):
        commit_transaction(SimpleNamespace(commit=failed_commit), suppressing)
