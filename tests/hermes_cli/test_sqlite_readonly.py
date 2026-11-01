"""Real SQLite WAL snapshots, permissions and no-initialization contracts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
import sqlite3

import pytest

from hermes_cli.sqlite_readonly import readonly_snapshot
from hermes_cli.sqlite_safe_read import has_live_connection


def _fixture(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA wal_autocheckpoint=0')
    conn.execute('CREATE TABLE records(value INTEGER)')
    conn.execute('INSERT INTO records VALUES (1)')
    return conn


def test_closed_wal_without_sidecars_needs_no_owner(tmp_path):
    path = tmp_path / 'closed.db'
    _fixture(path).close()
    assert not path.with_name(path.name + '-wal').exists()
    before = path.read_bytes()
    with readonly_snapshot(path) as reader:
        assert has_live_connection(path)
        assert reader.execute('SELECT value FROM records').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            reader.execute('UPDATE records SET value=9')
    assert not has_live_connection(path)
    assert path.read_bytes() == before


def test_wal_snapshot_excludes_uncommitted_and_later_commits(tmp_path):
    path = tmp_path / 'active.db'
    with closing(_fixture(path)) as writer:
        # This committed value remains in WAL; a raw main-file copy loses it.
        writer.execute('UPDATE records SET value=2')
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('UPDATE records SET value=3')
        with readonly_snapshot(path) as reader:
            assert reader.execute('SELECT value FROM records').fetchone()[0] == 2
            writer.execute('COMMIT')
            assert reader.execute('SELECT value FROM records').fetchone()[0] == 2
        with readonly_snapshot(path) as reader:
            assert reader.execute('SELECT value FROM records').fetchone()[0] == 3


def test_snapshot_is_pinned_before_caller_reads_and_other_thread_commits(tmp_path):
    path = tmp_path / 'threaded.db'
    with closing(_fixture(path)):
        with readonly_snapshot(path) as reader:
            def update():
                with closing(sqlite3.connect(path, isolation_level=None)) as writer:
                    writer.execute('UPDATE records SET value=4')
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(update).result(timeout=10)
            assert reader.execute('SELECT value FROM records').fetchone()[0] == 1
        with readonly_snapshot(path) as reader:
            assert reader.execute('SELECT value FROM records').fetchone()[0] == 4


@pytest.mark.platforms('posix')
@pytest.mark.skipif(hasattr(os, 'geteuid') and os.geteuid() == 0, reason='root bypasses fixture mode bits')
def test_readonly_sidecars_preserve_committed_wal(tmp_path):
    path = tmp_path / 'readonly.db'
    with closing(_fixture(path)) as writer:
        writer.execute('UPDATE records SET value=7')
        files = [path, path.with_name(path.name + '-wal'), path.with_name(path.name + '-shm')]
        for entry in files:
            entry.chmod(0o400)
        tmp_path.chmod(0o500)
        try:
            with readonly_snapshot(path) as reader:
                assert reader.execute('SELECT value FROM records').fetchone()[0] == 7
        finally:
            tmp_path.chmod(0o700)
            for entry in files:
                entry.chmod(0o600)


@pytest.mark.platforms('posix')
@pytest.mark.skipif(hasattr(os, 'geteuid') and os.geteuid() == 0, reason='root bypasses fixture mode bits')
def test_missing_sidecars_in_readonly_directory_refuse_without_fallback(tmp_path):
    path = tmp_path / 'closed.db'
    _fixture(path).close()
    before = path.read_bytes()
    entries = sorted(p.name for p in tmp_path.iterdir())
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(sqlite3.OperationalError):
            with readonly_snapshot(path):
                pytest.fail('missing WAL sidecars must not bypass directory permissions')
    finally:
        tmp_path.chmod(0o700)
    assert not has_live_connection(path)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == entries


@pytest.mark.parametrize('content', [None, b'not a sqlite database'])
def test_missing_or_invalid_database_is_not_created_or_repaired(tmp_path, content):
    path = tmp_path / 'invalid.db'
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(sqlite3.Error):
        with readonly_snapshot(path):
            pytest.fail('invalid database opened')
    assert not has_live_connection(path)
    assert path.read_bytes() == content if content is not None else not path.exists()


def test_caller_failure_closes_snapshot(tmp_path):
    path = tmp_path / 'failure.db'
    _fixture(path).close()
    with pytest.raises(ValueError, match='caller failed'):
        with readonly_snapshot(path):
            raise ValueError('caller failed')
    assert not has_live_connection(path)


def test_query_deadline_refuses_expensive_read_and_closes(tmp_path, monkeypatch):
    from hermes_cli import sqlite_readonly
    path = tmp_path / 'deadline.db'
    _fixture(path).close()
    with readonly_snapshot(path) as reader:
        monkeypatch.setattr(sqlite_readonly.time, 'monotonic', lambda: float('inf'))
        with pytest.raises(sqlite3.OperationalError, match='interrupted'):
            reader.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n').fetchone()
    assert not has_live_connection(path)
