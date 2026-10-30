"""A public hold cannot race an admitted dispatcher or proceed without its locks."""
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from hermes_state_runtime import RuntimeStoreError

from tests.hermes_cli.test_gateway_kanban_window import parse


@pytest.fixture
def selected(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    kb.create_board('owned')
    with closing(connect(board='owned')) as db:
        tid = kb.create_task(db, title='selected', assignee='default')
    (tmp_path / 'ESTOP').write_text('hold')
    return tmp_path, kb.kanban_db_path(board='owned').resolve(), tid


@pytest.mark.parametrize('kind', ['host', 'board'])
def test_begin_refuses_already_admitted_dispatcher(selected, kind, capsys):
    from hermes_cli import kanban_db_connect as locks
    from hermes_cli.gateway_kanban_window import cmd_gateway_drain, execute
    home, path, tid = selected
    lock = locks._dispatch_host_admission_lock() if kind == 'host' else locks._dispatch_tick_lock(path)
    with lock as acquired:
        assert acquired
        with pytest.raises(RuntimeStoreError):
            execute(parse('begin', '--board', 'owned', '--task-id', tid))
        assert not (home / '.drain_request.json').exists()
        with pytest.raises(SystemExit) as refused:
            cmd_gateway_drain(parse('begin', '--board', 'owned', '--task-id', tid))
        assert refused.value.code == 6
        assert 'kanban_window_dispatch_busy' in capsys.readouterr().out


@pytest.mark.parametrize('kind', ['host', 'board'])
@pytest.mark.parametrize('failure', ['open', 'mkdir', 'acquire'])
def test_begin_refuses_lock_file_failure(selected, monkeypatch, kind, failure):
    from hermes_cli.gateway_kanban_window import execute
    from hermes_cli import kanban_db_connect as locks
    home, path, tid = selected
    blocked = home / 'kanban' / 'dispatch-host.lock' if kind == 'host' else path.with_name(path.name + '.dispatch.lock')
    original = Path.open

    def open_path(self, *args, **kwargs):
        if self == blocked:
            raise PermissionError('synthetic lock file refusal')
        return original(self, *args, **kwargs)

    original_mkdir = Path.mkdir
    original_acquire = locks._try_lock_nb

    def mkdir_path(self, *args, **kwargs):
        if self == blocked.parent:
            raise PermissionError('synthetic lock directory refusal')
        return original_mkdir(self, *args, **kwargs)

    def acquire(handle):
        if Path(handle.name) == blocked:
            raise OSError('synthetic lock acquisition refusal')
        return original_acquire(handle)

    if failure == 'open':
        monkeypatch.setattr(Path, 'open', open_path)
    elif failure == 'mkdir':
        monkeypatch.setattr(Path, 'mkdir', mkdir_path)
    else:
        monkeypatch.setattr(locks, '_try_lock_nb', acquire)
    with pytest.raises(RuntimeStoreError):
        execute(parse('begin', '--board', 'owned', '--task-id', tid))
    assert not (home / '.drain_request.json').exists()


def test_dispatch_cannot_enter_between_validation_and_publication(selected, monkeypatch):
    from gateway import drain_kanban as window
    from hermes_cli import kanban_db_connect as locks
    from hermes_cli.gateway_kanban_window import execute
    home, path, tid = selected
    original = window.begin

    def publish(target, **kwargs):
        with locks._dispatch_host_admission_lock() as acquired:
            assert not acquired, 'dispatcher entered before hold publication'
        with locks._dispatch_tick_lock(path) as acquired:
            assert not acquired, 'board dispatcher entered before hold publication'
        return original(target, **kwargs)

    monkeypatch.setattr(window, 'begin', publish)
    result = execute(parse('begin', '--board', 'owned', '--task-id', tid))
    assert result['held'] and not result['armed']
    assert (home / '.drain_request.json').exists()


def test_begin_revalidates_state_after_admission_lock(selected, monkeypatch):
    from hermes_cli import kanban_db_connect as locks
    from hermes_cli.gateway_kanban_window import execute
    home, _path, tid = selected
    original = locks._dispatch_host_admission_lock

    @contextmanager
    def after_dispatch(*args, **kwargs):
        with original(*args, **kwargs) as acquired:
            # The previous dispatch completed immediately before begin acquired admission.
            with closing(locks.connect(board='owned')) as db:
                db.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
                db.commit()
            yield acquired

    monkeypatch.setattr(locks, '_dispatch_host_admission_lock', after_dispatch)
    with pytest.raises(RuntimeStoreError):
        execute(parse('begin', '--board', 'owned', '--task-id', tid))
    assert not (home / '.drain_request.json').exists()
