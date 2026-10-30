"""A parked text reviewer remains a reviewer after explicit unblock and reclaim."""
from contextlib import closing
import json

import pytest

from tests.gateway.test_kanban_text_result import fixture_claim


@pytest.mark.parametrize('phase', ['ready','review'])
def test_text_result_retains_claim_phase_through_unblock(tmp_path, monkeypatch, phase):
    from gateway.session_kanban_text_result import settle_text_result
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    policy,context,scope=fixture_claim(tmp_path,monkeypatch,phase)
    with closing(connect(board='owned')) as conn:
        assert kb._retry_status_for_run(conn,context['task_id'],context['run_id'])==phase
    settle_text_result(policy,scope,{'completed':True,'final_response':'Public bounded result.'})
    with closing(connect(board='owned')) as conn:
        assert kb.get_task(conn,context['task_id']).status=='blocked'
        assert kb.unblock_task(conn,context['task_id'])
        assert kb.get_task(conn,context['task_id']).status==phase
        task=(kb.claim_review_task if phase=='review' else kb.claim_task)(conn,context['task_id'])
        assert task.current_run_id!=context['run_id']
        assert kb._retry_status_for_run(conn,task.id,task.current_run_id)==phase
        row=conn.execute("SELECT payload FROM task_events WHERE task_id=? AND run_id=? AND kind='blocked' ORDER BY id DESC LIMIT 1",
                         (context['task_id'],context['run_id'])).fetchone()
        assert json.loads(row[0])['source_status']==phase
