"""Opaque quarantine adds physical stores to the census, never skips ownership."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from hermes_cli import kanban_db as kb, kanban_db_connect as kbc
from hermes_cli import kanban_db_workspace as workspace
from hermes_cli import kanban_archive_quarantine as quarantine


@pytest.fixture
def opaque(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(name, raising=False)
    kb.create_board('former-owner')
    result = kb.remove_board('former-owner', archive=True)
    directory = Path(result['new_path'])
    (directory/'board.json').unlink()  # Simulate the legacy state, not an operator repair.
    database = directory/'kanban.db'
    record = {'directory': directory.name,
              'directory_identity': list(quarantine._identity(directory.stat())),
              'files': {'kanban.db': {'identity': list(quarantine._identity(database.stat())),
                                    'sha256': hashlib.sha256(database.read_bytes()).hexdigest()}},
              'counts': {name: 0 for name in quarantine.REQUIRED}, 'evidence_sha256': 'a'*64,
              'original_data_version_changed': True, 'evidence_data_version_changed': True}
    path = quarantine.registration_path(directory.parent)
    path.parent.parent.mkdir(mode=0o700)
    path.parent.mkdir(mode=0o700)
    value = {'schema': quarantine.SCHEMA, 'scope': 'physical_store_only',
             'execution_admission': False, 'records': [record]}
    path.write_text(json.dumps(value)); path.chmod(0o600)
    return directory, database, path, value


@pytest.mark.platforms('posix')
def test_registered_store_is_scanned_repeatedly_and_new_data_refuses(opaque):
    directory, database, _path, _value = opaque
    with kbc.connect_closing() as conn:
        for _ in range(2):
            assert database in workspace._sibling_board_db_files(conn)
            assert not workspace._other_board_uses_path(database, 'unrelated-id', '/unowned')
            assert not (directory/'board.json').exists()
        with closing(sqlite3.connect(database)) as late:
            late.row_factory = sqlite3.Row
            task_id = kb.create_task(late, title='Late legitimate owner')
        with pytest.raises(OSError, match='opaque archive'):
            workspace._sibling_board_db_files(conn)
        assert workspace._workspace_in_use_by_other(conn, 'different-id', '/unowned') == 'unknown'
    with closing(sqlite3.connect(database)) as preserved:
        assert preserved.execute('SELECT COUNT(*) FROM tasks WHERE id=?', (task_id,)).fetchone()[0] == 1


@pytest.mark.parametrize('damage', ['revoked', 'bad_mode', 'path_traversal', 'nonempty_attestation',
                                    'inode', 'symlink', 'unregistered_sibling'])
@pytest.mark.platforms('posix')
def test_unqualified_registration_never_bypasses_unknown_archive_refusal(opaque, damage):
    directory, _database, path, value = opaque
    if damage == 'revoked':
        (path.parent/'revoked.json').write_text('{}')
    if damage == 'bad_mode':
        path.chmod(0o644)
    if damage == 'path_traversal':
        value['records'][0]['directory'] = '../outside'
    if damage == 'nonempty_attestation':
        value['records'][0]['counts']['tasks'] = 1
    if damage == 'inode':
        value['records'][0]['files']['kanban.db']['identity'][1] += 1
    if damage == 'symlink':
        target = path.with_name('other.json'); path.rename(target); path.symlink_to(target)
    if damage == 'unregistered_sibling':
        (directory.parent/'unknown-1234567890').mkdir()
    if damage in ('path_traversal', 'nonempty_attestation', 'inode'):
        path.write_text(json.dumps(value))
    with kbc.connect_closing() as conn, pytest.raises(OSError):
        workspace._sibling_board_db_files(conn)
