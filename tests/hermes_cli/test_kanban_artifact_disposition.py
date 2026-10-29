"""Accepted research artifacts can settle intake without completion side effects."""
import hashlib
import importlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows

ARTIFACT = b'Public research report; no experiment was executed.'
PROVENANCE = b'Versioned source register and independent content review.'
BOARD = 'artifact-review'


def _api():
    try:
        return importlib.import_module('hermes_cli.kanban_artifact_disposition')
    except ModuleNotFoundError as error:
        if error.name == 'hermes_cli.kanban_artifact_disposition':
            pytest.fail('No supported artifact-only disposition exists; source evidence requires a merged PR')
        raise


@pytest.fixture
def case(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(name, raising=False)
    kb.create_board(BOARD)
    with connect_closing(board=BOARD) as conn:
        tid = kb.create_task(conn, title='Synthetic research report', body='Review public sources',
                             initial_status='todo', assignee='task-intake-router', board=BOARD)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='triage' WHERE id=?", (tid,))
        kb.add_comment(conn, tid, 'reviewer', 'Preserve original intake history')
        kb.add_attachment(conn, tid, filename='retained.txt', stored_path='retained/synthetic.txt', size=1)
    return tid


def _decision(api, tid, revision):
    return api.ArtifactAcceptance(
        board=BOARD, task_id=tid, reviewed_revision=revision,
        artifact_sha256=hashlib.sha256(ARTIFACT).hexdigest(),
        provenance_sha256=hashlib.sha256(PROVENANCE).hexdigest(), accepted_by='research-owner')


def _apply(api, conn, tid, decision, **overrides):
    values = dict(board=BOARD, expected_revision=decision.reviewed_revision,
                  evidence=decision, artifact=ARTIFACT, provenance=PROVENANCE)
    values.update(overrides)
    return api.archive_accepted_artifact(conn, tid, **values)


def test_accepted_artifact_changes_only_status_and_one_digest_event(case, monkeypatch):
    api = _api()

    def forbidden(*args, **kwargs):
        pytest.fail('Artifact disposition invoked unrelated completion effects')

    for name in ('recompute_ready', '_cleanup_workspace', '_clear_failure_counter',
                 'notify_task_updated', '_fire_kanban_lifecycle_hook'):
        monkeypatch.setattr(kb, name, forbidden)
    with connect_closing(board=BOARD) as conn:
        before = _selected_task_rows(conn, [case])
        revision = task_revision(BOARD, case, before)
        decision = _decision(api, case, revision)
        assert _apply(api, conn, case, decision)
        after = _selected_task_rows(conn, [case])
        assert after['tasks'] == [dict(before['tasks'][0], status='archived')]
        for table in before.keys() - {'tasks', 'task_events'}:
            assert after[table] == before[table]
        assert after['task_events'][:-1] == before['task_events']
        event = after['task_events'][-1]
        payload = json.loads(event['payload'])
        assert event['kind'] == 'archived' and event['run_id'] is None
        assert payload['scope'] == 'artifact_only'
        assert payload['reviewed_revision'] == revision
        assert payload['artifact_sha256'] == hashlib.sha256(ARTIFACT).hexdigest()
        assert payload['provenance_sha256'] == hashlib.sha256(PROVENANCE).hexdigest()
        assert payload['acceptance_kind'] == 'deliverable_accepted'
        assert payload['runtime_acceptance_claimed'] is False
        assert payload['experiment_execution_claimed'] is False
        assert 'pull_request' not in payload and 'merge_sha' not in payload
        assert ARTIFACT.decode() not in event['payload']
        assert PROVENANCE.decode() not in event['payload']
        for _ in range(2):
            with pytest.raises(ValueError):
                _apply(api, conn, case, decision)
            assert _selected_task_rows(conn, [case]) == after


@pytest.mark.parametrize('change', [
    'late_comment', 'revision', 'board', 'card', 'acceptance_revision',
    'artifact', 'provenance', 'missing_input', 'mutable_artifact', 'oversize',
    'empty_provenance', 'bad_digest', 'bad_reviewer', 'delegated', 'event_failure', 'edited_comment',
    'invalid_composition',
])
def test_artifact_disposition_refuses_stale_unbound_or_unaccepted_evidence(case, monkeypatch, change):
    api = _api()
    with connect_closing(board=BOARD) as conn:
        revision = task_revision(BOARD, case, _selected_task_rows(conn, [case]))
        decision = _decision(api, case, revision)
        changes = {
            'card': {'task_id': 't_00000000'},
            'acceptance_revision': {'reviewed_revision': '0' * 64},
            'missing_input': {'acceptance_kind': 'missing_input_stop'},
            'bad_digest': {'artifact_sha256': 'not-a-digest'},
            'bad_reviewer': {'accepted_by': 'private\ncontent'},
        }
        if change in changes:
            decision = replace(decision, **changes[change])
        overrides = {
            'revision': {'expected_revision': '0' * 64},
            'board': {'board': 'other'}, 'artifact': {'artifact': b'Changed after acceptance'},
            'provenance': {'provenance': b'Changed provenance'},
            'mutable_artifact': {'artifact': bytearray(ARTIFACT)},
            'oversize': {'artifact': b'x' * (1024 * 1024 + 1)},
            'empty_provenance': {'provenance': b''},
            'invalid_composition': {'allow_nested': 1},
        }
        if change == 'late_comment':
            with connect_closing(board=BOARD) as other:
                kb.add_comment(other, case, 'other', 'New information after acceptance')
        if change == 'edited_comment':
            with kb.write_txn(conn):
                conn.execute("UPDATE task_comments SET body='Edited older evidence' WHERE task_id=?", (case,))
        if change in {'delegated', 'event_failure'}:
            def deny(*args, **kwargs):
                raise PermissionError('Synthetic guarded refusal')
            target = '_assert_not_delegated_child_mutation' if change == 'delegated' else '_append_event'
            monkeypatch.setattr(kb, target, deny)
        before = _selected_task_rows(conn, [case])
        error = PermissionError if change in {'delegated', 'event_failure'} else ValueError
        with pytest.raises(error):
            _apply(api, conn, case, decision, **overrides.get(change, {}))
        assert _selected_task_rows(conn, [case]) == before


def test_artifact_disposition_refuses_other_database_connection(case):
    api = _api()
    kb.create_board('different-board')
    with connect_closing(board=BOARD) as conn:
        revision = task_revision(BOARD, case, _selected_task_rows(conn, [case]))
        decision = _decision(api, case, revision)
    with connect_closing(board='different-board') as other:
        with pytest.raises(ValueError, match='canonical board'):
            _apply(api, other, case, decision)
    with connect_closing(board=BOARD) as conn:
        assert task_revision(BOARD, case, _selected_task_rows(conn, [case])) == revision


def test_competing_acceptances_archive_exactly_once(case):
    api = _api()
    with connect_closing(board=BOARD) as conn:
        before = _selected_task_rows(conn, [case])
        decision = _decision(api, case, task_revision(BOARD, case, before))
    ready = Barrier(2, timeout=10)

    def attempt():
        with connect_closing(board=BOARD) as conn:
            ready.wait()
            try:
                return _apply(api, conn, case, decision)
            except ValueError:
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == [False, True]
    with connect_closing(board=BOARD) as conn:
        after = _selected_task_rows(conn, [case])
        assert after['tasks'] == [dict(before['tasks'][0], status='archived')]
        assert len(after['task_events']) == len(before['task_events']) + 1


@pytest.mark.parametrize('finish', ['commit', 'rollback'])
def test_explicit_composition_keeps_storage_preflight_and_mutation_atomic(case, finish):
    api = _api()
    with connect_closing(board=BOARD) as conn:
        before = _selected_task_rows(conn, [case])
        decision = _decision(api, case, task_revision(BOARD, case, before))
        try:
            with kb.write_txn(conn):
                # The operator can now qualify the whole store while holding
                # the same writer reservation used for the selected mutation.
                assert conn.execute("SELECT count(*) FROM tasks WHERE status='running'").fetchone()[0] == 0
                assert _apply(api, conn, case, decision, allow_nested=True)
                assert conn.in_transaction
                if finish == 'rollback':
                    raise RuntimeError('Synthetic postflight failure before outer commit')
        except RuntimeError:
            if finish != 'rollback':
                raise
        after = _selected_task_rows(conn, [case])
        if finish == 'rollback':
            assert after == before
        else:
            assert after['tasks'] == [dict(before['tasks'][0], status='archived')]
            assert len(after['task_events']) == len(before['task_events']) + 1


def test_nested_artifact_disposition_requires_explicit_opt_in(case):
    api = _api()
    with connect_closing(board=BOARD) as conn:
        before = _selected_task_rows(conn, [case])
        decision = _decision(api, case, task_revision(BOARD, case, before))
        with kb.write_txn(conn):
            with pytest.raises(RuntimeError, match='Nested composition'):
                _apply(api, conn, case, decision)
        assert _selected_task_rows(conn, [case]) == before


def test_existing_artifact_connection_never_initializes_schema(case, monkeypatch):
    api = _api()
    from hermes_cli import kanban_db_connect as kbc

    def forbidden(*args, **kwargs):
        pytest.fail('Artifact connection invoked the normal schema initializer')

    monkeypatch.setattr(kbc, '_migrate_add_optional_columns', forbidden)
    with api.existing_artifact_connection(board=BOARD) as conn:
        before = list(conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        assert kb.get_task(conn, case).status == 'triage'
        assert list(conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name")) == before
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')


@pytest.mark.parametrize('existing', ['missing', 'empty', 'partial'])
def test_artifact_connection_preserves_missing_empty_and_partial_databases(tmp_path, monkeypatch, existing):
    api = _api()
    home = tmp_path / 'isolated'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(name, raising=False)
    database = kb.kanban_db_path(board='default')
    if existing == 'missing':
        with pytest.raises(ValueError):
            with api.existing_artifact_connection(board='default'):
                pytest.fail('Missing board was opened')
        assert not home.exists()
        return
    database.parent.mkdir(parents=True)
    if existing == 'empty':
        database.write_bytes(b'')
        with pytest.raises(ValueError):
            with api.existing_artifact_connection(board='default'):
                pytest.fail('Empty board was initialized')
        assert database.read_bytes() == b''
        return
    with sqlite3.connect(database) as fixture:
        fixture.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT)')
        fixture.execute("INSERT INTO tasks VALUES('t_00000001','triage')")
    with api.existing_artifact_connection(board='default') as conn:
        assert [row['name'] for row in conn.execute('PRAGMA table_info(tasks)')] == ['id', 'status']
        assert [tuple(row) for row in conn.execute('SELECT * FROM tasks')] == [('t_00000001', 'triage')]
        assert [row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ['tasks']


_OWNER_FIELDS = ('claim_lock', 'claim_expires', 'worker_pid', 'worker_started_at',
                 'current_run_id', 'started_at', 'last_heartbeat_at')
_CONTRACT_FIELDS = ('completion_contract', 'workflow_template_id', 'current_step_key', 'block_kind')


@pytest.mark.parametrize('change', [*_OWNER_FIELDS, *_CONTRACT_FIELDS, 'blocked', 'run',
                                   'link', 'sticky', 'privacy', 'loop', 'failures'])
def test_artifact_disposition_preserves_ownership_dependencies_and_holds(case, change):
    api = _api()
    with connect_closing(board=BOARD) as conn:
        fields = {**dict.fromkeys(_OWNER_FIELDS, 1), **dict.fromkeys(_CONTRACT_FIELDS, 'held'),
                  'blocked': 'blocked', 'failures': 1}
        field = {'blocked': 'status', 'failures': 'consecutive_failures'}.get(change, change)
        if change in fields:
            with kb.write_txn(conn):
                conn.execute(f'UPDATE tasks SET {field}=? WHERE id=?', (fields[change], case))
        elif change == 'run':
            with kb.write_txn(conn):
                conn.execute("INSERT INTO task_runs(task_id,status,started_at,ended_at,outcome) "
                             "VALUES(?,'completed',1,2,'completed')", (case,))
        elif change == 'link':
            parent = kb.create_task(conn, title='Unresolved dependency', board=BOARD)
            kb.link_tasks(conn, parent, case)
        else:
            events = {'sticky': ('blocked', {'sticky': True}),
                      'privacy': ('gave_up', {'local_privacy_denial': True}),
                      'loop': ('block_loop_detected', {})}
            kind, payload = events[change]
            with kb.write_txn(conn):
                kb._append_event(conn, case, kind, payload)
        before = _selected_task_rows(conn, [case])
        revision = task_revision(BOARD, case, before)
        with pytest.raises(ValueError):
            _apply(api, conn, case, _decision(api, case, revision))
        assert _selected_task_rows(conn, [case]) == before
