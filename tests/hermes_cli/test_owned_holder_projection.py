"""Native holder isolation retains real descriptors, legacy ownership and test guards."""
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from fastapi import HTTPException

from tests.hermes_cli.test_browse_schema_drift import _pre_merge_store, _profile_home

pytest_plugins = ['tests._fixtures.owned_native_holders']


@pytest.mark.platforms('posix')
@pytest.mark.spawns_gateway_lookalike
def test_owned_legacy_gateway_is_a_real_native_holder(tmp_path, owned_native_holders):
    import hermes_state_dbfile as dbfile
    import hermes_state_holders as holders
    from hermes_cli.web_server_sessions import _open_session_db_at_path

    path = _pre_merge_store(_profile_home(tmp_path) / 'state.db')
    script = tmp_path / 'hermes_cli' / 'main.py'
    script.parent.mkdir()
    script.write_text(
        'import sqlite3,sys\n'
        'conn=sqlite3.connect(sys.argv[-1])\n'
        'conn.execute("SELECT count(*) FROM sessions").fetchone()\n'
        'print("holding",flush=True)\n'
        'sys.stdin.read()\n')
    before = path.read_bytes()
    child = subprocess.Popen([sys.executable, str(script), 'gateway', 'run', str(path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'holding'
        assert child.pid not in owned_native_holders.pids()
        owned_native_holders.register(child.pid)
        assert child.pid in [pid for pid, _ in holders.foreign_state_db_holders(path)]
        targets = (dbfile._iter_darwin_fd_targets() if sys.platform == 'darwin'
                   else dbfile._iter_proc_fd_targets())
        native_pids = {row[0] for row in targets}
        assert child.pid in native_pids
        assert native_pids <= set(owned_native_holders.pids())
        with pytest.raises(HTTPException) as caught:
            _open_session_db_at_path(path, read_only=True)
        assert 'running gateway owns it' in caught.value.detail
        assert f'PID {child.pid}' in caught.value.detail
        assert path.read_bytes() == before
    finally:
        child.stdin.close()
        child.wait(timeout=10)


@pytest.mark.platforms('posix')
def test_projection_keeps_unknown_refusal_and_real_home_guards(tmp_path, owned_native_holders, monkeypatch):
    import hermes_state_holders as holders
    from tests import conftest

    path = tmp_path / 'state.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE retained (value TEXT)')
    before = path.read_bytes()

    def denied():
        raise PermissionError('owned fixture descriptor scan denied')

    monkeypatch.setattr(owned_native_holders, 'processes', denied)
    refusal = holders.held_store_refusal(path, command='repair')
    assert 'cannot prove the database is quiet' in refusal
    assert path.read_bytes() == before
    protected = tmp_path / 'protected-canary'
    protected.mkdir()
    canary = protected / 'file.txt'
    canary.write_text('unchanged')
    monkeypatch.setattr(conftest, '_REAL_HERMES_ROOT_CANDIDATES',
                        [*conftest._REAL_HERMES_ROOT_CANDIDATES, protected])
    with pytest.raises((AssertionError, pytest.fail.Exception), match='REAL hermes home'):
        canary.read_text()
    with pytest.raises((AssertionError, pytest.fail.Exception), match='REAL hermes home'):
        canary.write_text('changed')
