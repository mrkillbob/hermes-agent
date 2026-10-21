"""Overlapping recurrence keys must not create duplicate project audits."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError, Lock
from types import SimpleNamespace
from pathlib import Path

import pytest

from hermes_cli import kanban_audit_enqueue as producer


@pytest.fixture(autouse=True)
def isolated_board_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)


@pytest.fixture
def audit_project(tmp_path):
    from hermes_cli import projects_db, kanban_db
    with projects_db.connect_closing(tmp_path / 'projects.db') as conn:
        identity = projects_db.create_project(conn, name='existing', board_slug='default')
        project = projects_db.get_project(conn, identity)
    kanban_db.write_board_metadata('default', project_id=identity)
    return project


def test_overlapping_recurrences_create_only_one_audit(tmp_path, monkeypatch, audit_project):
    body = tmp_path / 'body.txt'
    body.write_text('audit')
    project = audit_project
    overlap = Barrier(2)
    guard = Lock()
    rows = []
    created = []

    def command(argv):
        if 'list' in argv:
            with guard:
                snapshot = list(rows)
            if not snapshot:
                try:
                    overlap.wait(timeout=2)
                except BrokenBarrierError:
                    pass  # A serialized caller cannot enter until the first creates.
            return snapshot
        with guard:
            task = dict(id=f't_{len(created)}', project_id=project.id,
                        created_by='audit', status='ready')
            created.append(task)
            rows.append(task)
        return task

    monkeypatch.setattr(producer, 'run_json', command)
    def invoke(key):
        return producer.enqueue(SimpleNamespace(project='existing', board=None,
            body_file=body, hermes='hermes', assignee='auditor', creator='audit',
            title='Audit', idempotency_key=key, max_runtime='6m'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(invoke, ['week-one', 'week-two']))
    assert len(created) == 1
    assert sum(result.get('reason') == 'audit_already_active' for result in outcomes) == 1


def test_missing_registry_is_not_created(tmp_path, monkeypatch):
    import pytest
    import sqlite3
    from hermes_cli import projects_db
    registry = tmp_path / 'projects.db'
    monkeypatch.setattr(projects_db, 'projects_db_path', lambda: registry)
    with pytest.raises(sqlite3.OperationalError):
        producer.resolve_project('hermes-agent', None)
    assert not registry.exists()


def test_archived_project_is_refused(tmp_path, monkeypatch):
    import pytest
    from hermes_cli import projects_db
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    with projects_db.connect_closing(tmp_path / 'projects.db') as conn:
        identity = projects_db.create_project(conn, name='Hermes', board_slug='default')
        conn.execute('UPDATE projects SET archived=1 WHERE id=?', (identity,))
        conn.commit()
    with pytest.raises(ValueError, match='archived'):
        producer.resolve_project(identity, None)


def test_existing_project_link_preserves_repository_workspace_policy(tmp_path, monkeypatch):
    import sqlite3
    from hermes_cli import projects_db
    from hermes_cli.kanban_project_link import resolve_project_link
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    repository = tmp_path / 'repository'
    repository.mkdir()
    with projects_db.connect_closing(tmp_path / 'projects.db') as conn:
        identity = projects_db.create_project(conn, name='Hermes', board_slug='default',
                                              primary_path=str(repository))
    from hermes_cli import kanban_db
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    kanban_db.write_board_metadata('default', project_id=identity)
    project = producer.resolve_project(identity, 'default')
    with sqlite3.connect(':memory:') as board:
        actual_id, _, repo, kind = resolve_project_link(board, project.id, None, 'scratch', None)
    assert (actual_id, repo, kind) == (identity, str(repository), 'worktree')


def test_unconfirmed_create_is_not_retried(tmp_path, monkeypatch, audit_project):
    import pytest
    body = tmp_path / 'body'
    body.write_text('audit')
    calls = []
    def command(argv):
        calls.append(argv)
        return [] if 'list' in argv else {'id': 't_uncertain', 'project_id': None}
    monkeypatch.setattr(producer, 'run_json', command)
    with pytest.raises(ValueError, match='original outcome'):
        producer.enqueue(SimpleNamespace(project='existing', board=None, body_file=body,
            hermes='hermes', assignee='auditor', creator='audit', title='Audit',
            idempotency_key='week', max_runtime='6m'))
    assert len(calls) == 2


def test_producer_package_is_in_sealed_distribution():
    import fnmatch
    import tomllib
    from pathlib import Path
    config = tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text())
    discovery = config['tool']['setuptools']['packages']['find']
    package = producer.__package__
    assert any(fnmatch.fnmatchcase(package, pattern) for pattern in discovery['include'])
    assert not any(fnmatch.fnmatchcase(package, pattern) for pattern in discovery.get('exclude', []))


def test_out_of_band_rebind_during_listing_does_not_create(tmp_path, monkeypatch):
    import pytest
    from hermes_cli import projects_db, kanban_db
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    with projects_db.connect_closing(tmp_path / 'projects.db') as conn:
        first = projects_db.create_project(conn, name='First', board_slug='default')
        second = projects_db.create_project(conn, name='Second', board_slug='default')
    kanban_db.write_board_metadata('default', project_id=first)
    body = tmp_path / 'body'
    body.write_text('audit')
    calls = []
    def list_and_rebind(argv):
        calls.append(argv)
        assert 'list' in argv
        import json
        path = kanban_db.board_metadata_path('default')
        metadata = json.loads(path.read_text())
        metadata['project_id'] = second
        path.write_text(json.dumps(metadata))  # Manual file edits do not honor API locks.
        return []
    monkeypatch.setattr(producer, 'run_json', list_and_rebind)
    with pytest.raises(ValueError, match='board ownership'):
        producer.enqueue(SimpleNamespace(project=first, board=None, body_file=body,
            hermes='hermes', assignee='auditor', creator='audit', title='Audit',
            idempotency_key='week', max_runtime='6m'))
    assert len(calls) == 1


def test_binding_writer_cannot_change_owner_during_create(tmp_path, monkeypatch):
    from threading import Event, Thread
    from hermes_cli import projects_db, kanban_db
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    with projects_db.connect_closing(tmp_path / 'projects.db') as conn:
        first = projects_db.create_project(conn, name='First', board_slug='default')
        second = projects_db.create_project(conn, name='Second', board_slug='default')
    kanban_db.write_board_metadata('default', project_id=first)
    body = tmp_path / 'body'
    body.write_text('audit')
    started, finished = Event(), Event()
    errors, writers, observed = [], [], []
    def bind():
        started.set()
        try:
            kanban_db.write_board_metadata('default', project_id=second)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()
    def command(argv):
        if 'list' in argv:
            return []
        writer = Thread(target=bind, daemon=True)
        writers.append(writer)
        writer.start()
        assert started.wait(5)
        finished.wait(2)  # Give an unlocked writer its chance to reproduce the race.
        observed.append(kanban_db.read_board_metadata('default')['project_id'])
        return {'id': 't_created', 'project_id': first}
    monkeypatch.setattr(producer, 'run_json', command)
    try:
        result = producer.enqueue(SimpleNamespace(project=first, board=None, body_file=body,
            hermes='hermes', assignee='auditor', creator='audit', title='Audit',
            idempotency_key='week', max_runtime='6m'))
    finally:
        for writer in writers:
            writer.join(10)
    assert result['project_id'] == first
    assert observed == [first], 'board ownership changed between validation and creation'
    assert finished.is_set() and not errors
    assert kanban_db.read_board_metadata('default')['project_id'] == second


def test_starting_audit_waits_for_in_progress_metadata_write(tmp_path, monkeypatch):
    from threading import Event, Thread
    from hermes_cli import projects_db, kanban_db, active_sessions
    from hermes_cli.kanban_db_boards import board_ownership_lock
    with projects_db.connect_closing(tmp_path / 'projects.db') as conn:
        identity = projects_db.create_project(conn, name='Existing', board_slug='default')
    kanban_db.write_board_metadata('default', project_id=identity)
    path = kanban_db.board_metadata_path('default')
    lock_path = board_ownership_lock('default').path
    partial, release, attempted, observed, finished = (Event() for _ in range(5))
    errors, results = [], []
    write_text = Path.write_text
    enter = active_sessions._FileLock.__enter__

    def held_write(target, data, *args, **kwargs):
        if target != path:
            return write_text(target, data, *args, **kwargs)
        with target.open('w', encoding='utf-8') as stream:
            stream.write('{')
            stream.flush()
            partial.set()
            if not release.wait(10):
                raise TimeoutError('test did not release metadata writer')
            stream.seek(0)
            stream.truncate()
            stream.write(data)
        return len(data)

    def noted_enter(lock):
        if lock.path == lock_path:
            attempted.set()
            observed.set()
        return enter(lock)

    def write():
        try:
            kanban_db.write_board_metadata('default', name='Updated display name')
        except Exception as exc:
            errors.append(exc)

    body = tmp_path / 'body'
    body.write_text('audit')
    monkeypatch.setattr(Path, 'write_text', held_write)
    writer = Thread(target=write, daemon=True)
    writer.start()
    assert partial.wait(5)
    monkeypatch.setattr(active_sessions._FileLock, '__enter__', noted_enter)
    monkeypatch.setattr(producer, 'run_json', lambda argv: [] if 'list' in argv else
                        {'id': 't_created', 'project_id': identity})

    def audit():
        try:
            results.append(producer.enqueue(SimpleNamespace(project=identity, board=None,
                body_file=body, hermes='hermes', assignee='auditor', creator='audit',
                title='Audit', idempotency_key='week', max_runtime='6m')))
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()
            observed.set()

    worker = Thread(target=audit, daemon=True)
    worker.start()
    try:
        assert observed.wait(5)
        assert attempted.is_set() and not finished.is_set(), 'audit read partial metadata before locking'
    finally:
        release.set()
        worker.join(10)
        writer.join(10)
    assert not errors and finished.is_set()
    assert results[0]['project_id'] == identity
