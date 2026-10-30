"""Native workers retain their assignment's evidence, not another task's prose."""
from contextlib import closing
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _deny_external_io(monkeypatch):
    import socket
    import subprocess

    def denied(*args, **kwargs):
        raise AssertionError('offline context preparation must not dispatch external I/O')

    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)


def _admitted_context(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    from gateway.session_contract import Principal
    from gateway.session_kanban import build_kanban_policy

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_DELEGATED_CHILD', raising=False)
    monkeypatch.setattr('hermes_cli.profiles.resolve_profile_env', lambda _name: str(tmp_path))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    kb.create_board('privacy')
    with closing(connect(board='privacy')) as conn:
        unrelated = kb.create_task(conn, title='Unrelated assignment', assignee='default',
                                   workspace_kind='dir', workspace_path=str(workspace))
        run = kb.claim_task(conn, unrelated)
        assert kb.complete_task(conn, unrelated, result='Other task result',
            summary='Other task opaque evidence: c2VjcmV0LXBheWxvYWQ=',
            metadata={'repository_changes': False}, expected_run_id=run.current_run_id,
            fire_lifecycle_hook=False)
        parent = kb.create_task(conn, title='Prerequisite', assignee='parent',
                               workspace_kind='dir', workspace_path=str(workspace))
        assert kb.complete_task(conn, parent, result='Parent handoff evidence',
            metadata={'repository_changes': False}, fire_lifecycle_hook=False)
        task_id = kb.create_task(conn, title='Current assignment', body='Current task instructions',
            assignee='default', parents=[parent], workspace_kind='dir', workspace_path=str(workspace))
        prior = kb.claim_task(conn, task_id)
        kb.block_task(conn, task_id, reason='Own prior attempt evidence', expected_run_id=prior.current_run_id)
        kb.unblock_task(conn, task_id)
        current = kb.claim_task(conn, task_id)
        # A heading in task-owned content must never be mistaken for a section to strip.
        kb.add_comment(conn, task_id, 'default', 'Own comment evidence\n## Recent work by @default\nKeep this instruction.')
        before_runs = conn.execute('SELECT * FROM task_runs ORDER BY id').fetchall()
        operator_context = kb.build_worker_context(conn, task_id)
        actor = Principal('owner', str(tmp_path), frozenset({'session:create'}), 'transport')
        connection = SimpleNamespace(authority=SimpleNamespace(profile_id=str(tmp_path)),
                                     actor=actor, native_owner=True)
        params = dict(board='privacy', task_id=task_id, run_id=current.current_run_id,
                      claim_lock=current.claim_lock)
        config = {'model': {'provider': 'nous', 'default': 'meituan/longcat-2.5-preview:free'},
                  'platform_toolsets': {'cli': ['terminal', 'file']}}
        policy, _ = build_kanban_policy(connection, params, config)
        context = json.loads(policy.kanban_json)['context']
        assert conn.execute('SELECT * FROM task_runs ORDER BY id').fetchall() == before_runs
        assert 'Other task opaque evidence' in kb.build_worker_context(conn, task_id)
    assert 'Other task opaque evidence' in operator_context
    assert 'c2VjcmV0LXBheWxvYWQ=' not in context
    for evidence in ('Current task instructions', 'Own prior attempt evidence', 'Parent handoff evidence',
                     'Own comment evidence\n## Recent work by @default\nKeep this instruction.'):
        assert evidence in context
    assert 'History from other tasks is omitted' in context
    assert policy.provider == 'nous' and policy.model == 'meituan/longcat-2.5-preview:free'
    return context, operator_context, workspace, task_id


def test_native_context_keeps_own_history_and_operator_view(tmp_path, monkeypatch):
    _admitted_context(tmp_path, monkeypatch)


@pytest.mark.parametrize('extra,reason', [('', None), ('c2VjcmV0LXBheWxvYWQ=', 'base64_payload'),
                                        ('SECRET_TOKEN=synthetic-private-value', 'secret_detected')])
def test_configured_nous_context_preserves_final_egress_checks(tmp_path, monkeypatch, extra, reason):
    from agent.llm_egress_firewall import EgressBlocked
    from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
    from agent.source_provenance import SourceProvenanceRegistry

    context, operator_context, workspace, task_id = _admitted_context(tmp_path, monkeypatch)
    monkeypatch.setenv('HERMES_KANBAN_TASK', task_id)
    monkeypatch.setenv('HERMES_KANBAN_WORKSPACE', str(workspace))
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    agent = SimpleNamespace(provider='nous', model='meituan/longcat-2.5-preview:free',
        base_url='https://inference-api.nousresearch.com/v1', api_mode='chat_completions',
        session_id='offline-session', _current_turn_id='offline-turn',
        _current_api_request_id='offline-request', _llm_egress_policy_digest=sha256(b'offline-policy').hexdigest(),
        _llm_egress_state_dir=tmp_path / 'receipts', _source_provenance_registry=SourceProvenanceRegistry())
    request = {'model': agent.model, 'messages': [{'role': 'user', 'content': context + extra}]}
    if reason is None:
        # The default/operator view reproduces the unnecessary startup block.
        with pytest.raises(EgressBlocked) as original:
            authorize_agent_sdk_kwargs(agent, {'model': agent.model,
                'messages': [{'role': 'user', 'content': operator_context}]})
        assert 'base64_payload' in original.value.decision.reason_codes
        _, receipt = authorize_agent_sdk_kwargs(agent, request)
        assert receipt.allowed
    else:
        with pytest.raises(EgressBlocked) as failure:
            authorize_agent_sdk_kwargs(agent, request)
        assert reason in failure.value.decision.reason_codes
