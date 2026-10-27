"""Source-only archival preserves unrelated state and refuses changed authority."""
import json

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows
from hermes_cli.kanban_source_supersession import archive_source_supersession


def _make_case(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path / 'home'))
    for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(name, raising=False)
    kb.create_board('source-review')
    with connect_closing(board='source-review') as conn:
        tid = kb.create_task(conn, title='Synthetic source finding', body='Preserve original content',
                             initial_status='todo', assignee='task-intake-router', board='source-review')
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='triage' WHERE id=?", (tid,))
        kb.add_comment(conn, tid, 'reviewer', 'Retained source evidence')
    return tid


@pytest.fixture
def case(tmp_path, monkeypatch):
    return _make_case(tmp_path, monkeypatch)


def decision():
    from hermes_cli.kanban_source_supersession import SourceSupersessionEvidence

    return SourceSupersessionEvidence(repository='mrkillbob/hermes-agent', pull_request=369,
                                      merge_sha='a' * 40, reason_code='source_finding_resolved',
                                      finding_comment_id=123)


def test_source_supersession_changes_only_status_and_one_audit_event(case, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('source-only archival invoked an unrelated side effect')

    for name in ('recompute_ready', '_cleanup_workspace', '_clear_failure_counter',
                 'notify_task_updated', '_fire_kanban_lifecycle_hook'):
        monkeypatch.setattr(kb, name, forbidden)
    with connect_closing(board='source-review') as conn:
        before = _selected_task_rows(conn, [case])
        revision = task_revision('source-review', case, before)
        assert archive_source_supersession(conn, case, board='source-review',
                                              expected_revision=revision, evidence=decision())
        after = _selected_task_rows(conn, [case])
        task = dict(after['tasks'][0])
        assert task.pop('status') == 'archived'
        assert task == {k: v for k, v in before['tasks'][0].items() if k != 'status'}
        for table in ('task_links', 'task_runs', 'task_comments', 'task_attachments', 'kanban_notify_subs'):
            assert after[table] == before[table]
        assert after['task_events'][:-1] == before['task_events']
        event = after['task_events'][-1]
        payload = json.loads(event['payload'])
        assert event['kind'] == 'archived' and event['run_id'] is None
        assert payload['scope'] == 'source_only' and payload['reviewed_revision'] == revision
        assert payload['merge_sha'] == 'a' * 40 and payload['finding_comment_id'] == 123
        assert payload['runtime_acceptance_claimed'] is False
        for _ in range(2):
            with pytest.raises(ValueError):
                archive_source_supersession(conn, case, board='source-review',
                                               expected_revision=revision, evidence=decision())
            assert _selected_task_rows(conn, [case]) == after


_FIELD_REFUSALS = {
    'ready': ('status', 'ready'),
    'claim': ('claim_lock', 'synthetic-owner'),
    'contract': ('completion_contract', 'mrkillbob/hermes-agent'),
    'workflow': ('workflow_template_id', 'fixture'),
    'failure': ('consecutive_failures', 1),
    'hold': ('block_kind', 'needs_input'),
}


def _change_field(conn, task_id, change):
    field, value = _FIELD_REFUSALS[change]
    with kb.write_txn(conn):
        conn.execute(f'UPDATE tasks SET {field}=? WHERE id=?', (value, task_id))


def _add_run(conn, task_id, change):
    with kb.write_txn(conn):
        conn.execute("INSERT INTO task_runs(task_id,status,started_at,ended_at,outcome) "
                     "VALUES(?,'completed',1,2,'completed')", (task_id,))


def _add_link(conn, task_id, change):
    parent = kb.create_task(conn, title='Retained dependency', board='source-review')
    kb.link_tasks(conn, parent, task_id)


def _add_sticky_event(conn, task_id, change):
    with kb.write_txn(conn):
        kb._append_event(conn, task_id, 'gave_up', {'sticky': True, 'local_privacy_denial': True})


def _add_block_loop(conn, task_id, change):
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (task_id,))
    for attempt in range(kb.BLOCK_RECURRENCE_LIMIT):
        assert kb.block_task(conn, task_id, kind=None, reason=None)
        if attempt + 1 < kb.BLOCK_RECURRENCE_LIMIT:
            assert kb.unblock_task(conn, task_id)
    assert kb.get_task(conn, task_id).status == 'triage'
    assert not _selected_task_rows(conn, [task_id])['task_runs']


def _add_explicit_lineage(conn, task_id, change):
    with kb.write_txn(conn):
        kb._append_event(conn, task_id, 'blocked', {'reason': None, 'kind': None})
    assert kb._has_sticky_block(conn, task_id)


_CARD_CHANGES = {**dict.fromkeys(_FIELD_REFUSALS, _change_field),
                 'run': _add_run, 'link': _add_link, 'sticky': _add_sticky_event,
                 'block_loop': _add_block_loop, 'explicit_lineage': _add_explicit_lineage}


@pytest.mark.parametrize('change', [*_CARD_CHANGES, 'revision', 'late_comment', 'board', 'delegated'])
def test_source_supersession_refuses_unqualified_or_stale_cards(case, monkeypatch, change):
    with connect_closing(board='source-review') as conn:
        apply_change = _CARD_CHANGES.get(change)
        if apply_change is not None:
            apply_change(conn, case, change)
        before_revision = _selected_task_rows(conn, [case])
        revision = task_revision('source-review', case, before_revision)
        if change == 'late_comment':
            with connect_closing(board='source-review') as other:
                kb.add_comment(other, case, 'other', 'Changed after review')
        before = _selected_task_rows(conn, [case])
        if change == 'delegated':
            def deny(*args, **kwargs):
                raise PermissionError('delegated writer forbidden')
            monkeypatch.setattr(kb, '_assert_not_delegated_child_mutation', deny)
        error = PermissionError if change == 'delegated' else ValueError
        with pytest.raises(error):
            archive_source_supersession(conn, case,
                                           board='other' if change == 'board' else 'source-review',
                                           expected_revision='0' * 64 if change == 'revision' else revision,
                                           evidence=decision())
        assert _selected_task_rows(conn, [case]) == before
