"""Browse deadlines include connection setup and transaction entry (PR #300 macOS CI)."""
import sqlite3
import time
from contextlib import closing

import pytest

import hermes_cli.sqlite_safe_read as safe_read
import hermes_state_reconcile as reconcile


@pytest.mark.parametrize('phase', [pytest.param('durability', marks=pytest.mark.platforms('macos')), 'begin'])
def test_expired_browse_setup_stops_sql_and_preserves_history(tmp_path, monkeypatch, phase):
    """A setup helper may swallow SQLite interruption; expiry must still release its caller.

    Advance the deadline at an actual SQL boundary instead of depending on a slow host. SQLite's
    real progress handler still interrupts execution; no connection or transaction is mocked.
    """
    path = tmp_path / 'state.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE retained (history TEXT)')
        conn.execute("INSERT INTO retained VALUES ('keep me')")
        conn.commit()
    before = path.read_bytes()
    clock = [0.0]
    budget = reconcile._Budget(3.0)
    budget._clock = lambda: clock[0]
    budget.deadline = 3.0
    statements = []
    interrupted = []
    expiring_sql = 'PRAGMA synchronous=FULL' if phase == 'durability' else 'BEGIN IMMEDIATE'

    class DeadlineConnection(sqlite3.Connection):
        def set_progress_handler(self, handler, n):
            # Observe every VM step so even BEGIN's short program crosses the deadline.
            return super().set_progress_handler(handler, 1)

        def execute(self, statement, *args):
            statements.append(statement)
            if statement == expiring_sql:
                clock[0] = 4.0
            try:
                return super().execute(statement, *args)
            except sqlite3.OperationalError:
                interrupted.append(statement)
                raise

    connect = safe_read.connect_tracked

    def open_tracked(*args, **kwargs):
        return connect(*args, **kwargs, factory=DeadlineConnection)

    monkeypatch.setattr(safe_read, 'connect_tracked', open_tracked)
    with pytest.raises(reconcile.BrowseBudgetExceeded):
        reconcile._apply_additive_schema(path, budget)
    assert budget.expired
    assert expiring_sql in interrupted
    assert ('BEGIN IMMEDIATE' in statements) == (phase == 'begin')
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT history FROM retained').fetchall() == [('keep me',)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('retained',)]
        conn.execute('BEGIN IMMEDIATE')  # the failed browse left no connection holding the store
        conn.rollback()


@pytest.mark.parametrize('phase', ['begin', 'commit'])
def test_browse_busy_wait_uses_budget_left_after_prior_work(tmp_path, monkeypatch, record_property, phase):
    """Real writer/reader locks must not grant transaction boundaries a fresh full budget.

    Offset only the budget's monotonic clock after real setup/DDL to model prior work without a
    sleep. SQLite's busy wait uses real elapsed time and the blocker stays held until unwinding.
    """
    from gateway.run_bootstrap import _reserve_waiting_out_browse_reconcile
    from gateway.runtime_ownership import ProfileOwnership, exclusive_maintenance
    import hermes_state_repair as repair
    import hermes_state_schema as schema

    path = tmp_path / 'state.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE retained (history TEXT)')
        conn.execute("INSERT INTO retained VALUES ('keep me')")
        conn.commit()
        assert conn.execute('PRAGMA journal_mode').fetchone() == ('delete',)
    before = path.read_bytes()
    budget = reconcile._Budget(reconcile.BROWSE_RECONCILE_BUDGET_S)
    consumed = [0.0]
    budget._clock = lambda: time.monotonic() + consumed[0]
    wait_started = []
    original_setup = repair._reapply_durability_barriers
    original_ddl = schema.apply_declared_schema

    def account_prior_work():
        consumed[0] = reconcile.BROWSE_RECONCILE_BUDGET_S - 0.4
        wait_started.append(time.monotonic())

    def setup(conn):
        original_setup(conn)
        if phase == 'begin':
            account_prior_work()

    def apply_ddl(cursor):
        original_ddl(cursor)
        if phase == 'commit':
            account_prior_work()

    monkeypatch.setattr(repair, '_reapply_durability_barriers', setup)
    monkeypatch.setattr(schema, 'apply_declared_schema', apply_ddl)
    with closing(sqlite3.connect(path, isolation_level=None)) as blocker:
        blocker.execute('BEGIN IMMEDIATE' if phase == 'begin' else 'BEGIN')
        blocker.execute('SELECT history FROM retained').fetchall()
        error = None
        with exclusive_maintenance([tmp_path], purpose=reconcile.BROWSE_RECONCILE_PURPOSE):
            lock_info = (tmp_path / 'gateway.lock').stat()
            try:
                reconcile._apply_additive_schema(path, budget)
            except (reconcile.BrowseBudgetExceeded, sqlite3.OperationalError) as exc:
                error = exc
        elapsed = time.monotonic() - wait_started[0]
        record_property('busy_wait_seconds', elapsed)
        record_property('expired', budget.expired)
        record_property('error', type(error).__name__)
        blocker.rollback()
    assert elapsed < 2.0, f'{phase} waited {elapsed:.3f}s with only 0.4s left: {error!r}'
    assert isinstance(error, reconcile.BrowseBudgetExceeded), (phase, elapsed, error, budget.expired)
    assert budget.expired
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT history FROM retained').fetchall() == [('keep me',)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('retained',)]
        conn.execute('BEGIN IMMEDIATE')
        conn.rollback()
    after = (tmp_path / 'gateway.lock').stat()
    assert (after.st_dev, after.st_ino) == (lock_info.st_dev, lock_info.st_ino)
    booting = ProfileOwnership()
    try:
        started = time.monotonic()
        _reserve_waiting_out_browse_reconcile(booting, [tmp_path])
        assert booting.owns(tmp_path)
        assert time.monotonic() - started < 2.0
    finally:
        booting.close()


@pytest.mark.parametrize('phase', ['execute', 'fetch'])
def test_expired_schema_interrupt_stops_before_another_statement(tmp_path, monkeypatch, phase):
    """The shared column reconciler catches interruption; a browse must not keep issuing SQL.

    Move only the deadline clock at a real schema execute/fetch boundary. SQLite's progress callback
    raises SQLITE_INTERRUPT, and native cursors record any later statement attempts.
    """
    from gateway.run_bootstrap import _reserve_waiting_out_browse_reconcile
    from gateway.runtime_ownership import ProfileOwnership, exclusive_maintenance

    path = tmp_path / 'state.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)')
        conn.execute("INSERT INTO sessions VALUES ('kept', 'cli')")
        conn.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)')
        conn.execute("INSERT INTO messages VALUES (1, 'history survives')")
        conn.commit()
    before = path.read_bytes()
    clock = [0.0]
    budget = reconcile._Budget(reconcile.BROWSE_RECONCILE_BUDGET_S)
    budget._clock = lambda: clock[0]
    budget.deadline = reconcile.BROWSE_RECONCILE_BUDGET_S
    interrupted = []
    after_expiration = []

    class DeadlineCursor(sqlite3.Cursor):
        def execute(self, statement, *args):
            if budget.expired:
                after_expiration.append(statement)
            self.statement = statement
            if phase == 'execute' and statement.startswith('ALTER TABLE "sessions" ADD COLUMN'):
                clock[0] = budget.deadline + 1
            try:
                return super().execute(statement, *args)
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
                    interrupted.append(statement)
                raise

        def fetchall(self):
            if phase == 'fetch' and self.statement == 'PRAGMA table_info("sessions")':
                clock[0] = budget.deadline + 1
            try:
                return super().fetchall()
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
                    interrupted.append(self.statement)
                raise

    class DeadlineConnection(sqlite3.Connection):
        def cursor(self):
            return super().cursor(factory=DeadlineCursor)

        def set_progress_handler(self, handler, n):
            return super().set_progress_handler(handler, 1)

    connect = safe_read.connect_tracked

    def open_tracked(*args, **kwargs):
        return connect(*args, **kwargs, factory=DeadlineConnection)

    monkeypatch.setattr(safe_read, 'connect_tracked', open_tracked)
    with exclusive_maintenance([tmp_path], purpose=reconcile.BROWSE_RECONCILE_PURPOSE):
        lock_info = (tmp_path / 'gateway.lock').stat()
        with pytest.raises(reconcile.BrowseBudgetExceeded):
            reconcile._apply_additive_schema(path, budget)
    assert budget.expired
    assert interrupted, 'the real SQLite VM must interrupt the schema statement or fetch'
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT id FROM sessions').fetchall() == [('kept',)]
        assert conn.execute('SELECT content FROM messages').fetchall() == [('history survives',)]
        assert [row[1] for row in conn.execute('PRAGMA table_info(sessions)')] == ['id', 'source']
        conn.execute('BEGIN IMMEDIATE')
        conn.rollback()
    after = (tmp_path / 'gateway.lock').stat()
    assert (after.st_dev, after.st_ino) == (lock_info.st_dev, lock_info.st_ino)
    booting = ProfileOwnership()
    try:
        _reserve_waiting_out_browse_reconcile(booting, [tmp_path])
        assert booting.owns(tmp_path)
    finally:
        booting.close()
    assert not after_expiration, (len(after_expiration), after_expiration[:3])
