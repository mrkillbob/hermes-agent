"""Reserved-default stubs cannot hide workspace owners or mutate legacy stores."""
import hashlib
from pathlib import Path
import sqlite3

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
from tests.hermes_cli import test_kanban_task_binding as binding_tests

pytestmark = pytest.mark.platforms("posix")

binding_case = binding_tests.binding_case
_correct = binding_tests._correct


def _stub():
    path = kb.boards_root() / kb.DEFAULT_BOARD / 'kanban.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_binding_preserves_zero_default_shadow_and_checks_real_archive(binding_case):
    shadow = _stub()
    identity = shadow.stat()
    kb.create_board('history')
    with connect_closing(board='history') as conn:
        archived_id = kb.create_task(conn, title='Retain genuine ownership history', board='history')
    archived = Path(kb.remove_board('history')['new_path']) / 'kanban.db'
    before = hashlib.sha256(archived.read_bytes()).hexdigest()
    result = _correct(binding_case)
    assert result['tasks'] == 1
    assert shadow.read_bytes() == b'' and shadow.stat().st_ino == identity.st_ino
    assert hashlib.sha256(archived.read_bytes()).hexdigest() == before
    with sqlite3.connect(f'{archived.as_uri()}?mode=ro', uri=True) as conn:
        assert conn.execute('SELECT id FROM tasks').fetchone() == (archived_id,)
    with connect_closing(board='beta') as conn:
        assert kb.get_task(conn, binding_case[4]).status == 'triage'


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_shadow_sidecar_refuses_before_recovery(binding_case, suffix):
    shadow = _stub()
    Path(str(shadow) + suffix).write_bytes(b'')
    with pytest.raises((OSError, ValueError, sqlite3.Error)):
        _correct(binding_case)
    assert not (binding_case[0] / 'before.tar.gz.source.sqlite3').exists()
    with connect_closing(board='alpha') as conn:
        assert kb.get_task(conn, binding_case[4]).status == 'triage'


def test_populated_shadow_retains_ordinary_owner_check(binding_case):
    shadow = _stub()
    target = str(binding_case[1][1] / '.worktrees' / binding_case[4])
    with sqlite3.connect(shadow) as conn:
        conn.execute('CREATE TABLE tasks(id TEXT,status TEXT,workspace_path TEXT)')
        conn.execute('CREATE TABLE task_links(parent_id TEXT,child_id TEXT)')
        conn.execute('INSERT INTO tasks VALUES(?,?,?)', ('other-owner', 'blocked', target))
    with pytest.raises(ValueError, match="another board's card"):
        _correct(binding_case)
    assert not (binding_case[0] / 'before.tar.gz.source.sqlite3').exists()


def test_unlocked_general_census_still_refuses_zero_shadow(binding_case):
    from hermes_cli import kanban_db_workspace as workspace

    shadow = _stub()
    with pytest.raises(sqlite3.OperationalError):
        workspace._other_board_uses_path(shadow, binding_case[4], '/unused')


@pytest.mark.parametrize('change', ['replace', 'write', 'sidecar', 'unlock'])
def test_empty_shadow_proof_rechecks_identity_and_lock(binding_case, change):
    from hermes_cli.kanban_db_boards import board_ownership_lock
    from hermes_cli.kanban_default_shadow import guarded_default_shadow

    shadow = _stub()
    with board_ownership_lock(kb.DEFAULT_BOARD) as lock:
        with guarded_default_shadow(lock) as proof:
            assert proof.proves_empty(shadow)
            if change == 'replace':
                shadow.rename(shadow.with_suffix('.retained'))
                shadow.touch()
            elif change == 'write':
                shadow.write_bytes(b'not empty')
            elif change == 'sidecar':
                Path(str(shadow) + '-wal').touch()
            else:
                lock.__exit__(None, None, None)
            with pytest.raises(OSError):
                proof.proves_empty(shadow)


def test_aliased_shadow_refuses_without_modifying_target(binding_case):
    shadow = _stub()
    target = binding_case[0] / 'retained-empty'
    shadow.rename(target)
    shadow.symlink_to(target)
    with pytest.raises(OSError, match='non-aliased'):
        _correct(binding_case)
    assert target.read_bytes() == b'' and shadow.is_symlink()


def test_real_archive_owner_still_blocks_with_empty_default_shadow(binding_case):
    _stub()
    kb.create_board('history')
    target = str(binding_case[1][1] / '.worktrees' / binding_case[4])
    with connect_closing(board='history') as conn:
        owner = kb.create_task(conn, title='Keep borrowed workspace', board='history')
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked',workspace_path=? WHERE id=?", (target, owner))
    archive = Path(kb.remove_board('history')['new_path']) / 'kanban.db'
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="another board's card"):
        _correct(binding_case)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before
    assert not (binding_case[0] / 'before.tar.gz.source.sqlite3').exists()
