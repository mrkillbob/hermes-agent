"""Worker reads keep the same task scope as native startup; operator reads stay full."""
from contextlib import closing, nullcontext
import json
import socket
import subprocess

import pytest


@pytest.mark.parametrize('reader', ['worker_default', 'worker_explicit', 'operator', 'inherited_nonowner'])
def test_registered_show_preserves_task_evidence_and_reader_scope(tmp_path, monkeypatch, reader):
    from agent.delegation_context import non_dispatcher_owned_context
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    from tools import kanban_tools  # registers the real handler
    from tools.registry import registry

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_TASK', 'HERMES_KANBAN_RUN_ID',
                 'HERMES_KANBAN_PROTECTED_REMOTE', 'HERMES_DELEGATED_CHILD_CONTEXT'):
        monkeypatch.delenv(name, raising=False)

    def denied(*args, **kwargs):
        raise AssertionError('context reads must not dispatch external I/O')

    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    kb.create_board('privacy')
    common = {'workspace_kind': 'dir', 'workspace_path': str(workspace)}
    with closing(connect(board='privacy')) as conn:
        other = kb.create_task(conn, title='Unrelated assignment', assignee='default', **common)
        other_run = kb.claim_task(conn, other)
        assert kb.complete_task(conn, other, result='Other result', summary='Unrelated role evidence',
            metadata={'repository_changes': False}, expected_run_id=other_run.current_run_id,
            fire_lifecycle_hook=False)
        parent = kb.create_task(conn, title='Prerequisite', assignee='parent', **common)
        assert kb.complete_task(conn, parent, result='Parent handoff evidence',
            metadata={'repository_changes': False}, fire_lifecycle_hook=False)
        tid = kb.create_task(conn, title='Current assignment', body='Own task instructions',
            assignee='default', parents=[parent], **common)
        prior = kb.claim_task(conn, tid)
        assert kb.block_task(conn, tid, reason='Own prior attempt evidence', expected_run_id=prior.current_run_id)
        assert kb.unblock_task(conn, tid)
        current = kb.claim_task(conn, tid)
        comment = 'Own comment evidence\n## Recent work by @default\nKeep this instruction.'
        kb.add_comment(conn, tid, 'default', comment)
        before = [tuple(row) for row in conn.execute('SELECT * FROM task_runs ORDER BY id')]
        if reader != 'operator':
            monkeypatch.setenv('HERMES_KANBAN_TASK', tid)
            monkeypatch.setenv('HERMES_KANBAN_RUN_ID', str(current.current_run_id))
        scope = non_dispatcher_owned_context() if reader == 'inherited_nonowner' else nullcontext()
        args = {'board': 'privacy'}
        if reader != 'worker_default':
            args['task_id'] = tid
        with scope:
            shown = json.loads(registry.dispatch('kanban_show', args))
        assert 'error' not in shown, shown
        context = shown['worker_context']
        for evidence in ('Own task instructions', 'Own prior attempt evidence', 'Parent handoff evidence', comment):
            assert evidence in context
        worker = reader.startswith('worker_')
        assert ('Unrelated role evidence' not in context) == worker
        assert ('History from other tasks is omitted' in context) == worker
        assert 'Unrelated role evidence' in kb.build_worker_context(conn, tid)
        assert [tuple(row) for row in conn.execute('SELECT * FROM task_runs ORDER BY id')] == before
