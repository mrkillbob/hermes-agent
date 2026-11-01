"""Source acceptance never releases an execution hold or changes lifecycle state."""
from dataclasses import replace
import hashlib
import json

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_source_acceptance import (
    SourceAcceptance, record_source_acceptance, source_hold_bindings,
)
from hermes_cli.kanban_source_resolution import archive_resolved_source_task
from hermes_cli.kanban_source_supersession import SourceSupersessionEvidence
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows

_DOCUMENT = b'Synthetic local equivalent accepted; upstream remains pending; execution remains held.'
_SOURCE = SourceSupersessionEvidence('example/project', 123, 'a' * 40, 'source_finding_resolved')
_EVIDENCE = SourceAcceptance(_SOURCE, hashlib.sha256(_DOCUMENT).hexdigest(), 'reviewer',
                             'local_equivalent_upstream_pending', False)


@pytest.fixture
def case(tmp_path, monkeypatch):
    for key in ('HERMES_HOME', 'HERMES_KANBAN_HOME'):
        monkeypatch.setenv(key, str(tmp_path / 'home'))
    for key in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    kb.create_board('source-review')
    with connect_closing(board='source-review') as conn:
        tid = kb.create_task(conn, title='Synthetic source finding', initial_status='todo',
                             board='source-review')
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked',consecutive_failures=2,"
                         "last_failure_error='retained privacy refusal' WHERE id=?", (tid,))
            conn.execute("INSERT INTO task_runs(task_id,status,started_at,ended_at,outcome) "
                         "VALUES(?,'crashed',1,2,'crashed')", (tid,))
            kb._append_event(conn, tid, 'gave_up', {'sticky': True, 'error': 'retained refusal'})
    return tid


def arguments(conn, tid):
    rows = _selected_task_rows(conn, [tid])
    return dict(board='source-review', expected_revision=task_revision('source-review', tid, rows),
                evidence=_EVIDENCE, acceptance=_DOCUMENT, reviewed_hold_events=source_hold_bindings(rows))


def test_receipt_preserves_hold_and_all_history_without_lifecycle_effects(case, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('nonterminal source receipt invoked a lifecycle effect')
    for name in ('recompute_ready', '_cleanup_workspace', '_clear_failure_counter', '_end_run',
                 'notify_task_updated', '_fire_kanban_lifecycle_hook'):
        monkeypatch.setattr(kb, name, forbidden)
    with connect_closing(board='source-review') as conn:
        before = _selected_task_rows(conn, [case])
        assert kb._has_sticky_block(conn, case)
        assert record_source_acceptance(conn, case, **arguments(conn, case))
        after = _selected_task_rows(conn, [case])
        assert kb._has_sticky_block(conn, case)
        for table in before.keys() - {'task_events'}:
            assert after[table] == before[table]
        assert after['task_events'][:-1] == before['task_events']
        event = after['task_events'][-1]
        assert event['kind'] == 'source_acceptance' and event['run_id'] is None
        receipt = json.loads(event['payload'])
        assert receipt['scope_decision'] == 'local_equivalent_upstream_pending'
        assert receipt['upstream_milestone_fulfilled'] is False
        assert receipt['worker_admission_granted'] is False
        assert receipt['reviewed_hold_events'] == [list(v) for v in source_hold_bindings(before)]
        with pytest.raises(ValueError, match='already recorded'):
            record_source_acceptance(conn, case, **arguments(conn, case))
        with pytest.raises(ValueError, match='policy holds'):
            archive_resolved_source_task(conn, case, board='source-review', evidence=_SOURCE,
                expected_revision=task_revision('source-review', case, after))
        assert _selected_task_rows(conn, [case]) == after


@pytest.mark.parametrize('change', ['revision', 'hold_binding', 'document', 'board', 'active',
                                   'unfinished', 'external_contract', 'dependency', 'hold_payload',
                                   'trigger', 'delegated', 'milestone', 'missing_attestor'])
def test_refusals_leave_no_receipt_or_other_changes(case, monkeypatch, change):
    with connect_closing(board='source-review') as conn:
        args = arguments(conn, case)
        with kb.write_txn(conn):
            if change == 'revision':
                kb._append_event(conn, case, 'commented', {'changed': True})
            if change == 'active':
                conn.execute("UPDATE tasks SET claim_lock='new-owner' WHERE id=?", (case,))
            if change == 'unfinished':
                conn.execute('UPDATE task_runs SET ended_at=NULL WHERE task_id=?', (case,))
            if change == 'external_contract':
                conn.execute("UPDATE tasks SET completion_contract='external' WHERE id=?", (case,))
            if change == 'hold_payload':
                conn.execute("UPDATE task_events SET payload='[]' WHERE task_id=? AND kind='gave_up'", (case,))
            if change == 'trigger':
                conn.execute("CREATE TRIGGER unwanted AFTER INSERT ON task_events BEGIN "
                             "UPDATE tasks SET status='ready' WHERE id=NEW.task_id; END")
        if change == 'dependency':
            other = kb.create_task(conn, title='Separate unfinished work', board='source-review')
            kb.link_tasks(conn, case, other)
        if change not in ('revision', 'hold_payload'):
            args = arguments(conn, case)
        if change == 'hold_payload':
            args['expected_revision'] = task_revision('source-review', case, _selected_task_rows(conn, [case]))
        if change == 'hold_binding':
            args['reviewed_hold_events'] = ()
        if change == 'document':
            args['acceptance'] = b'different acceptance'
        if change == 'board':
            args['board'] = 'other-board'
        if change == 'milestone':
            args['evidence'] = replace(_EVIDENCE, upstream_milestone_fulfilled=True)
        if change == 'missing_attestor':
            args['evidence'] = replace(_EVIDENCE, accepted_by='')
        if change == 'delegated':
            def deny(*args, **kwargs):
                raise PermissionError('delegated writer refused')
            monkeypatch.setattr(kb, '_assert_not_delegated_child_mutation', deny)
        before = _selected_task_rows(conn, [case])
        with pytest.raises(PermissionError if change == 'delegated' else ValueError):
            record_source_acceptance(conn, case, **args)
        assert _selected_task_rows(conn, [case]) == before


def test_event_failure_rolls_back_and_nested_transaction_is_not_committed(case, monkeypatch):
    with connect_closing(board='source-review') as conn:
        before = _selected_task_rows(conn, [case])
        original = kb._append_event
        def fail_after_insert(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('synthetic receipt failure')
        with monkeypatch.context() as patch:
            patch.setattr(kb, '_append_event', fail_after_insert)
            with pytest.raises(RuntimeError, match='synthetic receipt failure'):
                record_source_acceptance(conn, case, **arguments(conn, case))
        assert _selected_task_rows(conn, [case]) == before
        with kb.write_txn(conn):
            with pytest.raises(RuntimeError, match='nested'):
                record_source_acceptance(conn, case, **arguments(conn, case))
        assert _selected_task_rows(conn, [case]) == before
