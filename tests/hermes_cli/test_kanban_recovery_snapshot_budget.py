"""The disclosed backup byte cap covers committed WAL and one pinned snapshot."""
import sqlite3
from pathlib import Path

import pytest

from hermes_cli.kanban_task_binding import _private_recovery_snapshot


def test_budget_rejects_logical_wal_size_before_publishing_recovery(tmp_path):
    source, recovery = tmp_path / 'source.db', tmp_path / 'recovery.db'
    with sqlite3.connect(source) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE records(value BLOB)')
        writer.execute('INSERT INTO records VALUES(zeroblob(20000))')
        writer.commit()
        assert source.stat().st_size <= 8192
        with pytest.raises(ValueError, match='budget'):
            _private_recovery_snapshot(source, recovery, max_bytes=8192)
        assert not recovery.exists()
        assert not list(tmp_path.glob('.binding-recovery-*'))
        assert writer.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 1


def test_budgeted_backup_keeps_its_snapshot_across_later_writer_commit(tmp_path, monkeypatch):
    source, recovery = tmp_path / 'source.db', tmp_path / 'recovery.db'
    connect = sqlite3.connect
    with connect(source) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE records(value BLOB)')
        writer.execute("INSERT INTO records VALUES('first')")
        writer.commit()
        calls = []

        def interleave(database, *args, **kwargs):
            connection = connect(database, *args, **kwargs)
            filename = connection.execute('PRAGMA database_list').fetchone()[2]
            if Path(filename) != source:
                # Destination opens only after source page-count/size qualification.
                writer.execute('INSERT INTO records VALUES(zeroblob(20000))')
                writer.commit()
                calls.append('later commit')
            return connection

        monkeypatch.setattr(sqlite3, 'connect', interleave)
        _private_recovery_snapshot(source, recovery, max_bytes=8192)
        assert calls == ['later commit']
        assert recovery.stat().st_size <= 8192
        with connect(recovery) as saved:
            assert saved.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 1
        assert writer.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 2
