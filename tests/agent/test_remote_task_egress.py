"""Owned remote tasks minimize replay without changing local tool execution."""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from agent.chat_completion_helpers import _dispatch_provider_request
from agent.llm_egress_firewall import EgressBlocked
from agent.source_provenance import SourceProvenanceRegistry


def _agent(tmp_path, endpoint='https://llm.example.test/v1'):
    return SimpleNamespace(provider='nvidia', model='public-test-model', base_url=endpoint,
        api_mode='chat_completions', session_id='session-1', _current_turn_id='turn-1',
        _current_api_request_id='request-1', _llm_egress_policy_digest=sha256(b'policy').hexdigest(),
        _llm_egress_state_dir=tmp_path/'receipts', _source_provenance_registry=SourceProvenanceRegistry())


@pytest.fixture
def owned_task(monkeypatch):
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_public')
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    monkeypatch.delenv('HERMES_DELEGATED_CHILD_CONTEXT', raising=False)
    import agent.llm_egress_runtime as runtime
    monkeypatch.setattr(runtime, 'egress_enforcement_enabled', lambda: True)
    monkeypatch.setattr(runtime, '_exact_provider_secret_values', lambda: ())


@pytest.mark.parametrize('endpoint,mode,expected', [
    ('https://llm.example.test/v1', 'chat_completions', True),
    ('http://127.0.0.1:8000/v1', 'chat_completions', False),
    ('http://[::1]:8000/v1', 'chat_completions', False),
    ('http://localhost:8000/v1', 'chat_completions', True),
    ('http://192.168.1.2/v1', 'chat_completions', True),
    (None, 'chat_completions', True),
    ('invalid', 'chat_completions', True),
    (None, 'in_process', False),
])
def test_owned_route_uses_actual_destination(tmp_path, owned_task, endpoint, mode, expected):
    from agent.remote_task_context import owned_remote_task
    agent = _agent(tmp_path, endpoint)
    agent.api_mode = mode
    assert owned_remote_task(agent) is expected


def test_route_override_and_inherited_nonowner(tmp_path, owned_task):
    from agent.delegation_context import non_dispatcher_owned_context
    from agent.remote_task_context import owned_remote_task
    agent = _agent(tmp_path, 'http://127.0.0.1:8000/v1')
    route = {'provider': 'nvidia', 'base_url': 'https://remote.example.test/v1', 'api_mode': 'chat_completions'}
    assert owned_remote_task(agent, route=route)
    with non_dispatcher_owned_context():
        assert not owned_remote_task(agent, route=route)


def test_real_local_kanban_show_projects_only_provider_replay(tmp_path, monkeypatch, owned_task):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    from tools import kanban_tools
    from tools.registry import registry

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path/'home'))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_RUN_ID', raising=False)
    workspace=tmp_path/'private-workspace';workspace.mkdir()
    kb.create_board('privacy')
    with closing(connect(board='privacy')) as conn:
        tid=kb.create_task(conn,title='Public audit',body='Read public evidence.',assignee='default',
            workspace_kind='dir',workspace_path=str(workspace))
        current=kb.claim_task(conn,tid)
        monkeypatch.setenv('HERMES_KANBAN_TASK',tid)
        monkeypatch.setenv('HERMES_KANBAN_RUN_ID',str(current.current_run_id))
        monkeypatch.setenv('HERMES_KANBAN_WORKSPACE',str(workspace))
        original=registry.dispatch('kanban_show',{'board':'privacy'})
        assert json.loads(original)['task']['workspace_path']==str(workspace)
        before=[tuple(row)for row in conn.execute('SELECT * FROM tasks WHERE id=?',(tid,))]
        request={'model':'public-test-model','messages':[
            {'role':'assistant','tool_calls':[{'id':'show-1','type':'function',
                'function':{'name':'kanban_show','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'show-1','content':original}]}
        untouched=deepcopy(request);sent=[]
        _dispatch_provider_request(_agent(tmp_path),request,lambda body:sent.append(dict(body)))
        replay=sent[0]['messages'][1]['content']
        assert replay.startswith('kanban_show completed locally.')
        assert 'Read public evidence.' in replay
        assert str(workspace) not in replay
        assert request==untouched
        assert json.loads(original)['task']['workspace_path']==str(workspace)
        assert before==[tuple(row)for row in conn.execute('SELECT * FROM tasks WHERE id=?',(tid,))]


@pytest.mark.parametrize('content,reason',[
    ('c2VjcmV0LXBheWxvYWQ=','base64_payload'),
    ('SECRET_TOKEN=synthetic-private-value','secret_detected'),
])
def test_remote_user_negatives_never_reach_callback(tmp_path, owned_task, content, reason):
    called=[]
    with pytest.raises(EgressBlocked) as blocked:
        _dispatch_provider_request(_agent(tmp_path),{'model':'public-test-model',
            'messages':[{'role':'user','content':content}]},lambda body:called.append(body))
    assert reason in blocked.value.decision.reason_codes
    assert not called


def test_unknown_destination_never_reaches_callback(tmp_path, owned_task):
    called=[]
    with pytest.raises(EgressBlocked) as blocked:
        _dispatch_provider_request(_agent(tmp_path,None),{'model':'public-test-model',
            'messages':[{'role':'user','content':'Return result 42.'}]},lambda body:called.append(body))
    assert 'unknown_destination' in blocked.value.decision.reason_codes
    assert not called


@pytest.mark.parametrize('scope',['operator','local_task','inherited_nonowner'])
def test_unaffected_contexts_keep_callback_contract(tmp_path, monkeypatch, owned_task, scope):
    from agent.delegation_context import non_dispatcher_owned_context
    from contextlib import nullcontext
    agent=_agent(tmp_path)
    if scope=='operator':monkeypatch.delenv('HERMES_KANBAN_TASK')
    if scope=='local_task':agent.base_url='http://127.0.0.1:8000/v1'
    request={'model':'public-test-model','messages':[{'role':'user','content':'Local operator context.'}]}
    with non_dispatcher_owned_context() if scope=='inherited_nonowner' else nullcontext():
        assert _dispatch_provider_request(agent,request,lambda body:body) is request


def test_explicit_operator_disabled_enforcement_is_not_redefined(tmp_path, monkeypatch, owned_task):
    import agent.llm_egress_runtime as runtime
    monkeypatch.setattr(runtime,'egress_enforcement_enabled',lambda:False)
    request={'model':'public-test-model','messages':[{'role':'user','content':'Local test text.'}]}
    assert _dispatch_provider_request(_agent(tmp_path),request,lambda body:body)==request


def test_relative_real_file_read_replays_source_without_changing_tool(tmp_path, monkeypatch, owned_task):
    from agent.source_provenance_tools import attach_trusted_source_provenance_metadata, source_provenance_activation
    from agent.tool_dispatch_helpers import make_tool_result_message
    from tools.file_tools import read_file_tool

    workspace=tmp_path/'workspace';workspace.mkdir()
    source=workspace/'source.py';source.write_text('first = 1\nsecond = 2\n')
    monkeypatch.chdir(workspace)
    monkeypatch.setenv('TERMINAL_CWD',str(workspace))
    monkeypatch.setenv('HERMES_KANBAN_WORKSPACE',str(workspace))
    agent=_agent(tmp_path);agent._current_api_request_id='turn-1:api:1'
    with source_provenance_activation(agent,'read_file'):
        result=read_file_tool('source.py',task_id='remote-real-read')
    assert 'first = 1' in result and 'second = 2' in result
    metadata=attach_trusted_source_provenance_metadata(agent,'read_file',content=result)
    message=make_tool_result_message('read_file',result,'read-1',source_provenance=metadata)
    agent._current_api_request_id='turn-1:api:2'
    request={'model':agent.model,'messages':[message]};before=deepcopy(request);sent=[]
    _dispatch_provider_request(agent,request,lambda body:sent.append(dict(body)))
    replay=sent[0]['messages'][0]
    assert 'first = 1' in replay['content'] and 'second = 2' in replay['content']
    assert str(workspace) not in json.dumps(sent[0])
    assert '_source_provenance' not in replay
    assert request==before
    assert source.read_text()=='first = 1\nsecond = 2\n'
