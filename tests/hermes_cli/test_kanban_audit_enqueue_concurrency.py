"""Overlapping recurrence keys must not create duplicate project audits."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError, Lock
from types import SimpleNamespace

from hermes_cli import kanban_audit_enqueue as producer


def test_overlapping_recurrences_create_only_one_audit(tmp_path, monkeypatch):
    body = tmp_path / 'body.txt'
    body.write_text('audit')
    project = SimpleNamespace(id='p_existing', board_slug='default')
    monkeypatch.setattr(producer, 'resolve_project', lambda *_: project)
    from hermes_cli import projects_db
    monkeypatch.setattr(projects_db, 'projects_db_path', lambda: tmp_path / 'projects.db')
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
    project = producer.resolve_project(identity, 'default')
    with sqlite3.connect(':memory:') as board:
        actual_id, _, repo, kind = resolve_project_link(board, project.id, None, 'scratch', None)
    assert (actual_id, repo, kind) == (identity, str(repository), 'worktree')


def test_unconfirmed_create_is_not_retried(tmp_path, monkeypatch):
    import pytest
    from hermes_cli import projects_db
    monkeypatch.setattr(projects_db, 'projects_db_path', lambda: tmp_path / 'projects.db')
    project = SimpleNamespace(id='p_existing', board_slug='default')
    monkeypatch.setattr(producer, 'resolve_project', lambda *_: project)
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
