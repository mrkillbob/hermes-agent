"""Source-only disposition retains settled failures and refuses remaining authority."""
import json

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_source_supersession import SourceSupersessionEvidence
from hermes_cli.kanban_source_resolution import archive_resolved_source_task as resolve
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows


@pytest.fixture
def case(tmp_path, monkeypatch):
    for key in ('HERMES_HOME', 'HERMES_KANBAN_HOME'):
        monkeypatch.setenv(key, str(tmp_path / 'home'))
    for key in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    kb.create_board('source-review')
    with connect_closing(board='source-review') as conn:
        tid = kb.create_task(conn, title='Synthetic resolved source defect', body='Retained scope',
                             initial_status='todo', assignee='task-intake-router', board='source-review')
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked',started_at=1,consecutive_failures=2,"
                         "last_failure_error='retained failure' WHERE id=?", (tid,))
            for i in (1, 2):
                conn.execute("INSERT INTO task_runs(task_id,status,started_at,ended_at,outcome) "
                             "VALUES(?,'failed',?,?, 'retained failure')", (tid, i, i + 1))
            kb._append_event(conn, tid, 'gave_up', {'sticky': False})
        kb.add_comment(conn, tid, 'fixture', 'Retain this evidence')
    return tid


def apply(conn, task_id, **overrides):
    args = {'board': 'source-review', 'expected_revision': task_revision(
        'source-review', task_id, _selected_task_rows(conn, [task_id])),
        'evidence': SourceSupersessionEvidence('mrkillbob/hermes-agent', 435, 'a' * 40, 'source_finding_resolved')}
    return resolve(conn, task_id, **(args | overrides))


def test_settled_failures_archive_without_any_other_effect(case, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('source resolution invoked completion/readiness/cleanup/run effects')
    for name in ('recompute_ready', '_cleanup_workspace', '_clear_failure_counter', '_end_run',
                 'notify_task_updated', '_fire_kanban_lifecycle_hook'):
        monkeypatch.setattr(kb, name, forbidden)
    with connect_closing(board='source-review') as conn:
        before = _selected_task_rows(conn, [case])
        assert apply(conn, case)
        after = _selected_task_rows(conn, [case])
        assert after['tasks'] == [{**before['tasks'][0], 'status': 'archived'}]
        for table in before.keys() - {'tasks', 'task_events'}:
            assert after[table] == before[table]
        assert after['task_events'][:-1] == before['task_events']
        event = after['task_events'][-1]
        payload = json.loads(event['payload'])
        assert event['kind'] == 'archived' and event['run_id'] is None
        assert payload['scope'] == 'source_only' and not payload['runtime_acceptance_claimed']
        assert payload['failure_state_preserved'] and payload['workspace_retained']
        assert payload['prior_status'] == 'blocked' and payload['prior_run_count'] == 2
        assert not payload['readiness_recomputed']
        for _ in range(2):
            with pytest.raises(ValueError):
                apply(conn, case)
            assert _selected_task_rows(conn, [case]) == after


_FIELDS = {'active': ('status', 'running'), 'claim': ('claim_lock', 'fixture'),
           'expires': ('claim_expires', 99), 'pid': ('worker_pid', 123),
           'fingerprint': ('worker_started_at', 1), 'current_run': ('current_run_id', 1),
           'heartbeat': ('last_heartbeat_at', 1), 'workflow': ('workflow_template_id', 'fixture'),
           'step': ('current_step_key', 'fixture'), 'contract': ('completion_contract', 'external'),
           'block_kind': ('block_kind', 'needs_input'), 'recurrence': ('block_recurrences', 1)}


@pytest.mark.parametrize('change', [*_FIELDS, 'sticky', 'privacy', 'provider', 'malformed',
    'block_loop', 'explicit_block', 'dependency', 'unfinished', 'unknown_run', 'late_comment',
    'board', 'delegated', 'reason'])
def test_refuse_without_changes(case, monkeypatch, change):
    with connect_closing(board='source-review') as conn:
        with kb.write_txn(conn):
            if change in _FIELDS:
                field, value = _FIELDS[change]
                conn.execute(f'UPDATE tasks SET {field}=? WHERE id=?', (value, case))
            if change in ('sticky', 'privacy', 'provider', 'malformed', 'block_loop', 'explicit_block'):
                payload = {'sticky': change == 'sticky', 'local_privacy_denial': change == 'privacy',
                           'failure_class': 'provider_egress_blocked' if change == 'provider' else None}
                kind = {'block_loop': 'block_loop_detected', 'explicit_block': 'blocked'}.get(change, 'gave_up')
                kb._append_event(conn, case, kind, payload)
                if change == 'malformed':
                    conn.execute("UPDATE task_events SET payload='not-json' WHERE task_id=? AND id=(SELECT max(id) FROM task_events)", (case,))
            if change in ('unfinished', 'unknown_run'):
                column, value = ('ended_at', None) if change == 'unfinished' else ('status', 'unknown')
                conn.execute(f'UPDATE task_runs SET {column}=? WHERE task_id=?', (value, case))
        if change == 'dependency':
            other = kb.create_task(conn, title='Unreleased dependency', board='source-review')
            kb.link_tasks(conn, other, case)
        revision = task_revision('source-review', case, _selected_task_rows(conn, [case]))
        if change == 'late_comment':
            kb.add_comment(conn, case, 'fixture', 'Changed after review')
        if change == 'delegated':
            def deny(*args, **kwargs):
                raise PermissionError('delegated writer refused')
            monkeypatch.setattr(kb, '_assert_not_delegated_child_mutation', deny)
        before = _selected_task_rows(conn, [case])
        kwargs = {'expected_revision': revision}
        if change == 'board':
            kwargs['board'] = 'other'
        if change == 'reason':
            kwargs['evidence'] = SourceSupersessionEvidence('mrkillbob/hermes-agent', 435, 'a' * 40, 'informational_source_receipt')
        with pytest.raises(PermissionError if change == 'delegated' else ValueError):
            apply(conn, case, **kwargs)
        assert _selected_task_rows(conn, [case]) == before


def test_real_preparation_failures_preserve_spawn_failed_and_breaker_runs(case, monkeypatch):
    from hermes_cli.kanban_db_dispatch import _record_task_failure
    with connect_closing(board='source-review') as conn:
        tid = kb.create_task(conn, title='Synthetic accepted source after preparation failures',
                             initial_status='todo', assignee='task-intake-router', board='source-review')
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        for attempt in range(2):
            claimed = kb.claim_task(conn, tid, claimer=f'synthetic-owner-{attempt}', board='source-review')
            assert claimed is not None
            tripped = _record_task_failure(conn, tid, 'synthetic preparation failure',
                outcome='spawn_failed', failure_limit=2, release_claim=True, end_run=True,
                expected_run_id=claimed.current_run_id, expected_claim_lock=claimed.claim_lock)
            assert tripped is (attempt == 1)
        before = _selected_task_rows(conn, [tid])
        assert [run['status'] for run in before['task_runs']] == ['spawn_failed', 'gave_up']
        assert all(type(run['ended_at']) is int and run['ended_at'] > 0 for run in before['task_runs'])
        assert before['tasks'][0]['consecutive_failures'] == 2
        assert not kb._has_sticky_block(conn, tid)
        def forbidden(*args, **kwargs):
            pytest.fail('source resolution changed settled failure evidence or invoked cleanup')
        for name in ('_end_run', '_clear_failure_counter', '_cleanup_workspace', 'recompute_ready'):
            monkeypatch.setattr(kb, name, forbidden)
        assert apply(conn, tid)
        after = _selected_task_rows(conn, [tid])
        assert after['tasks'] == [{**before['tasks'][0], 'status': 'archived'}]
        for table in before.keys() - {'tasks', 'task_events'}:
            assert after[table] == before[table]
        assert after['task_events'][:-1] == before['task_events']
        for _ in range(2):
            with pytest.raises(ValueError):
                apply(conn, tid)
            assert _selected_task_rows(conn, [tid]) == after


@pytest.mark.parametrize('status', ['spawn_failed', 'gave_up'])
def test_preparation_status_without_end_remains_ineligible(case, status):
    with connect_closing(board='source-review') as conn:
        with kb.write_txn(conn):
            conn.execute('UPDATE task_runs SET status=?,ended_at=NULL WHERE task_id=?', (status, case))
        before = _selected_task_rows(conn, [case])
        with pytest.raises(ValueError, match='unfinished or unknown run history'):
            apply(conn, case)
        assert _selected_task_rows(conn, [case]) == before
