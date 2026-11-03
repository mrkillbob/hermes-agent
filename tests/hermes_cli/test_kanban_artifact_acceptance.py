"""Accepted reports do not convert denied execution into successful work."""
from dataclasses import replace
import hashlib
import json

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_artifact_acceptance import (
    RestrictedArtifactAcceptance, artifact_acceptance_view, comment_digest,
    criterion_digest, record_restricted_artifact_acceptance,
)
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_inspect import inspect_census, inspect_deliverables, main
from hermes_cli.kanban_source_acceptance import source_hold_bindings
from hermes_cli.kanban_source_resolution import _require_settled_source
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows

_BOARD = 'artifact-review'
_ARTIFACT = b'Synthetic completed read-only audit; no deployment.'
_PROVENANCE = b'Synthetic independent criterion review.'


@pytest.fixture
def case(tmp_path, monkeypatch):
    for key in ('HERMES_HOME', 'HERMES_KANBAN_HOME'):
        monkeypatch.setenv(key, str(tmp_path / 'home'))
    for key in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    kb.create_board(_BOARD)
    with connect_closing(board=_BOARD) as conn:
        tid = kb.create_task(conn, title='Audit and plan only', initial_status='todo', board=_BOARD)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked',consecutive_failures=1,"
                         "last_failure_error='retained denial',workspace_path='retained-path' WHERE id=?", (tid,))
            conn.execute("INSERT INTO task_runs(task_id,status,started_at,ended_at,outcome) "
                         "VALUES(?,'crashed',1,2,'crashed')", (tid,))
            kb._append_event(conn, tid, 'needs_attention', {'failure_class': 'provider_egress_blocked'})
            kb._append_event(conn, tid, 'gave_up', {'sticky': True, 'local_privacy_denial': True,
                'failure_class': 'provider_egress_blocked'})
        kb.add_comment(conn, tid, 'reviewer', 'Synthetic audit accepted; execution remains restricted.')
    return tid


def arguments(conn, tid):
    rows = _selected_task_rows(conn, [tid])
    comment = rows['task_comments'][-1]
    evidence = RestrictedArtifactAcceptance(hashlib.sha256(_ARTIFACT).hexdigest(),
        hashlib.sha256(_PROVENANCE).hexdigest(), criterion_digest(rows), comment['id'],
        comment_digest(comment), 'reviewer')
    return dict(board=_BOARD, expected_revision=task_revision(_BOARD, tid, rows), evidence=evidence,
                artifact=_ARTIFACT, provenance=_PROVENANCE, reviewed_hold_events=source_hold_bindings(rows))


def test_acceptance_is_one_event_without_execution_or_lifecycle_changes(case, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('receipt called lifecycle/process effect')
    with connect_closing(board=_BOARD) as conn:
        before = _selected_task_rows(conn, [case])
        stats = kb.board_stats(conn)
        with monkeypatch.context() as patch:
            for name in ('recompute_ready', '_cleanup_workspace', '_clear_failure_counter', '_end_run',
                         'notify_task_updated', '_fire_kanban_lifecycle_hook', '_terminate_reclaimed_worker'):
                patch.setattr(kb, name, forbidden)
            count = conn.total_changes
            assert record_restricted_artifact_acceptance(conn, case, **arguments(conn, case))
            assert conn.total_changes == count + 1
        after = _selected_task_rows(conn, [case])
        for table in before.keys() - {'task_events'}:
            assert after[table] == before[table]
        assert after['task_events'][:-1] == before['task_events']
        assert kb._has_sticky_block(conn, case)
        assert kb.board_stats(conn) == stats
        assert kb.claim_task(conn, case, board=_BOARD) is None
        kb.recompute_ready(conn)
        assert kb.get_task(conn, case).status == 'blocked'
        assert kb.gc_events(conn, older_than_seconds=0) == 0
        assert kb.delete_archived_task(conn, case) is False
        with pytest.raises(ValueError, match='policy holds'):
            _require_settled_source(after)
        with pytest.raises(ValueError, match='already recorded'):
            record_restricted_artifact_acceptance(conn, case, **arguments(conn, case))
        assert _selected_task_rows(conn, [case]) == after
    with connect_closing(board=_BOARD) as conn:
        view = artifact_acceptance_view(_BOARD, case, _selected_task_rows(conn, [case]))
        assert view['deliverable_accepted'] and view['execution_status'] == 'blocked'
        assert view['execution_authorized'] is False


@pytest.mark.parametrize('change', ['revision', 'artifact', 'provenance', 'criterion', 'comment',
    'hold', 'board', 'active', 'unfinished', 'contract', 'dependency', 'subscription',
    'trigger', 'delegated', 'unknown_hold', 'missing_privacy', 'attestor'])
def test_refusals_are_atomic(case, monkeypatch, change):
    with connect_closing(board=_BOARD) as conn:
        args = arguments(conn, case)
        if change == 'dependency':
            child = kb.create_task(conn, title='Child', board=_BOARD)
            kb.link_tasks(conn, case, child)
        with kb.write_txn(conn):
            if change == 'revision':
                conn.execute("UPDATE tasks SET priority=7 WHERE id=?", (case,))
            if change == 'active':
                conn.execute("UPDATE tasks SET claim_lock='owner' WHERE id=?", (case,))
            if change == 'unfinished':
                conn.execute('UPDATE task_runs SET ended_at=NULL WHERE task_id=?', (case,))
            if change == 'contract':
                conn.execute("UPDATE tasks SET completion_contract='external' WHERE id=?", (case,))
            if change == 'subscription':
                conn.execute("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES(?,'test','test',1)", (case,))
            if change == 'unknown_hold':
                kb._append_event(conn, case, 'blocked', {'reason': 'other decision'})
            if change == 'missing_privacy':
                conn.execute("UPDATE task_events SET payload='{}' WHERE task_id=? AND kind='gave_up'", (case,))
            if change == 'trigger':
                conn.execute("CREATE TRIGGER unwanted AFTER INSERT ON task_events BEGIN "
                             "UPDATE tasks SET status='ready' WHERE id=NEW.task_id; END")
        if change != 'revision':
            args = arguments(conn, case)
        if change in ('artifact', 'provenance'):
            args[change] = b'changed'
        if change in ('criterion', 'comment'):
            args['evidence'] = replace(args['evidence'], **{change + '_sha256': '0' * 64})
        if change == 'attestor':
            args['evidence'] = replace(args['evidence'], accepted_by='')
        if change == 'hold':
            args['reviewed_hold_events'] = ()
        if change == 'board':
            args['board'] = 'different'
        if change == 'delegated':
            def deny(*args, **kwargs):
                raise PermissionError('delegated mutation refused')
            monkeypatch.setattr(kb, '_assert_not_delegated_child_mutation', deny)
        before = _selected_task_rows(conn, [case])
        with pytest.raises(PermissionError if change == 'delegated' else ValueError):
            record_restricted_artifact_acceptance(conn, case, **args)
        assert _selected_task_rows(conn, [case]) == before


def test_failure_after_insert_rolls_back_and_nested_transaction_refuses(case, monkeypatch):
    with connect_closing(board=_BOARD) as conn:
        before = _selected_task_rows(conn, [case])
        original = kb._append_event
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('synthetic post-insert failure')
        with monkeypatch.context() as patch:
            patch.setattr(kb, '_append_event', fail)
            with pytest.raises(RuntimeError, match='post-insert'):
                record_restricted_artifact_acceptance(conn, case, **arguments(conn, case))
        assert _selected_task_rows(conn, [case]) == before
        with kb.write_txn(conn):
            with pytest.raises(RuntimeError, match='nested'):
                record_restricted_artifact_acceptance(conn, case, **arguments(conn, case))
        assert _selected_task_rows(conn, [case]) == before


@pytest.mark.parametrize('change', ['body', 'comment', 'hold', 'malformed', 'permission'])
def test_projection_fails_closed_after_evidence_or_receipt_changes(case, change):
    with connect_closing(board=_BOARD) as conn:
        record_restricted_artifact_acceptance(conn, case, **arguments(conn, case))
        with kb.write_txn(conn):
            if change == 'body':
                conn.execute("UPDATE tasks SET body='new criterion' WHERE id=?", (case,))
            if change == 'comment':
                conn.execute("UPDATE task_comments SET body='changed' WHERE task_id=?", (case,))
            if change == 'hold':
                kb._append_event(conn, case, 'gave_up', {'sticky': True, 'local_privacy_denial': True,
                    'failure_class': 'provider_egress_blocked'})
            if change == 'malformed':
                conn.execute("UPDATE task_events SET payload='[]' WHERE task_id=? AND kind='artifact_acceptance'", (case,))
            if change == 'permission':
                row = conn.execute("SELECT id,payload FROM task_events WHERE task_id=? AND kind='artifact_acceptance'", (case,)).fetchone()
                payload = json.loads(row['payload']); payload['execution_authorized'] = True
                conn.execute('UPDATE task_events SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
        assert not artifact_acceptance_view(_BOARD, case, _selected_task_rows(conn, [case]))['deliverable_accepted']


def test_explicit_read_view_filters_selected_ids_without_altering_census(case, capsys):
    with connect_closing(board=_BOARD) as conn:
        other = kb.create_task(conn, title='Unaccepted', board=_BOARD)
        record_restricted_artifact_acceptance(conn, case, **arguments(conn, case))
    db = kb.kanban_db_path(board=_BOARD)
    census = inspect_census(db, _BOARD)
    for selection, ids in [('all', [case, other]), ('accepted', [case]), ('unaccepted', [other])]:
        result = inspect_deliverables(db, _BOARD, [case, other], selection)
        assert [c['task_id'] for c in result['cards']] == ids
        assert result['selected_deliverable_counts'] == {'accepted': 1, 'unaccepted': 1}
    assert inspect_census(db, _BOARD) == census
    assert main(['--database', str(db), '--board', _BOARD, '--deliverables', 'accepted', case, other]) == 0
    assert json.loads(capsys.readouterr().out)['cards'][0]['artifact_acceptance']['execution_status'] == 'blocked'


def test_accepted_artifact_does_not_satisfy_child_dependency(case):
    with connect_closing(board=_BOARD) as conn:
        record_restricted_artifact_acceptance(conn, case, **arguments(conn, case))
        child = kb.create_task(conn, title='Dependent work', parents=[case], board=_BOARD)
        kb.recompute_ready(conn)
        assert not kb._parents_satisfied(conn, child)
        assert kb.claim_task(conn, child, board=_BOARD) is None
        assert not artifact_acceptance_view(_BOARD, case, _selected_task_rows(conn, [case]))['deliverable_accepted']


@pytest.mark.parametrize('change', ['schema', 'context_revision', 'execution_authorized'])
def test_invalid_newest_receipt_cannot_resurrect_older_acceptance(case, change):
    with connect_closing(board=_BOARD) as conn:
        record_restricted_artifact_acceptance(conn, case, **arguments(conn, case))
        rows = _selected_task_rows(conn, [case])
        payload = json.loads(rows['task_events'][-1]['payload'])
        payload[change] = True if change == 'execution_authorized' else 'invalid'
        with kb.write_txn(conn):
            kb._append_event(conn, case, 'artifact_acceptance', payload)
        assert not artifact_acceptance_view(_BOARD, case, _selected_task_rows(conn, [case]))['deliverable_accepted']
