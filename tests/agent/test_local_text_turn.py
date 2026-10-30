"""Real AIAgent finalization leaves the exact text claim for owner settlement."""
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROGRAM = '''
import json,os,socket,subprocess,sys
from types import SimpleNamespace
from agent.managed_worker import bind_worker_policy
frame=json.load(sys.stdin)
bind_worker_policy(frame)
context=json.loads(frame['policy']['kanban_json'])
os.environ.update(HERMES_KANBAN_TASK=context['task_id'],HERMES_KANBAN_DB=context['db'],
    HERMES_KANBAN_BOARD='owned',HERMES_KANBAN_RUN_ID=str(context['run_id']),
    HERMES_KANBAN_CLAIM_LOCK=context['claim_lock'])
from hermes_cli.version_info import get_version_info
get_version_info()
import platform
platform.platform()  # Prime SDK host headers before measuring turn IO (macOS uses uname).
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
with connect_closing(Path(context['db'])) as conn,kb.write_txn(conn):
    kb._append_event(conn,context['task_id'],'worker_bound',
        {'pid':os.getpid(),'claim_lock':context['claim_lock']},run_id=context['run_id'])
    kb._append_event(conn,context['task_id'],'owner_admitted',{'session_id':'owned-session'},run_id=context['run_id'])
attempts=[]
native_attempts=[]
def forbidden(*args,**kwargs):
    import traceback
    native_attempts.append([x.name for x in traceback.extract_stack(limit=10)])
    raise AssertionError('real socket or subprocess forbidden during agent turn')
socket.socket.connect=forbidden
socket.getaddrinfo=forbidden
socket.create_connection=forbidden
subprocess.Popen=forbidden
import httpx,run_agent
def response(request):
    import traceback
    body=json.loads(request.content) if request.content else {}
    attempts.append(dict(body,_fixture_url=str(request.url),_fixture_stack=[x.name for x in traceback.extract_stack(limit=12)]))
    assert request.url.host=='127.0.0.1'
    assert body['model']=='public-model' and not body.get('tools')
    if body.get('stream'):
        chunks=[{'id':'fixture','object':'chat.completion.chunk','created':0,'model':'public-model',
            'choices':[{'index':0,'delta':{'role':'assistant','content':'Public bounded answer.'},'finish_reason':None}]},
            {'id':'fixture','object':'chat.completion.chunk','created':0,'model':'public-model',
            'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}]
        data=''.join('data: '+json.dumps(x)+'\\n\\n' for x in chunks)+'data: [DONE]\\n\\n'
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=data)
    return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':0,'model':'public-model',
        'choices':[{'index':0,'message':{'role':'assistant','content':'Public bounded answer.'},'finish_reason':'stop'}],
        'usage':{'prompt_tokens':5,'completion_tokens':4,'total_tokens':9}})
# Keep the actual SDK/client construction and budget hooks; replace only wire IO.
httpx.HTTPTransport.handle_request=lambda self,request:response(request)
agent=run_agent.AIAgent(model='public-model',provider='llamacpp',api_key='no-key-required',
    base_url='http://127.0.0.1:8000/v1',api_mode='chat_completions',enabled_toolsets=[],fallback_model={},
    max_iterations=1,quiet_mode=True,skip_context_files=True,skip_memory=True,
    skip_background_review=True,load_soul_identity=False)
result=agent.run_conversation('Return one public bounded answer.')
with connect_closing(Path(context['db'])) as conn:
    before=kb.get_task(conn,context['task_id']).status
from gateway.session_kanban_text_result import settle_text_result
settlement=None
try:
    settle_text_result(SimpleNamespace(**frame['policy']),{'pid':os.getpid(),'session_id':'owned-session'},result)
except Exception as exc:
    settlement=type(exc).__name__+':'+str(exc)
with connect_closing(Path(context['db'])) as conn:
    after=kb.get_task(conn,context['task_id']).status
    outcome=conn.execute('SELECT outcome FROM task_runs WHERE id=?',(context['run_id'],)).fetchone()[0]
try:
    agent.client.chat.completions.create(model='public-model',messages=[{'role':'user','content':'second request must refuse'}])
except Exception:
    pass
from agent.managed_request_budget import _current_budget
budget=_current_budget()
print(json.dumps({'calls':len(attempts),'before':before,'after':after,'outcome':outcome,
    'response':result.get('final_response'),'failed':bool(result.get('failed')),
    'interrupted':bool(result.get('interrupted')),'settlement_error':settlement,
    'extra_refused':budget.stopped and budget.reason=='worker_request_budget_exhausted','native_attempts':native_attempts,
    'request_shapes':[{'url':x['_fixture_url'],'keys':sorted(x),'stream':x.get('stream'),
        'max_tokens':x.get('max_tokens'),'roles':[m.get('role') for m in x.get('messages',[])],
        'stack':x['_fixture_stack']} for x in attempts]}))
'''


@pytest.mark.parametrize('streaming', [False, True])
def test_actual_agent_turn_keeps_claim_until_owner_parks_text(tmp_path, monkeypatch, streaming):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    kb.create_board('owned')
    with closing(connect(board='owned')) as conn:
        tid=kb.create_task(conn,title='Public text control',assignee='default')
        task=kb.claim_task(conn,tid)
    context=dict(db=str(kb.kanban_db_path(board='owned')),task_id=tid,run_id=task.current_run_id,claim_lock=task.claim_lock,
        execution_budget=dict(wall_seconds=60,output_bytes=8192,requests=1,local_text_only=True),
        route=dict(provider='llamacpp',model='public-model',base_url='http://127.0.0.1:8000/v1'))
    frame={'safe_mode':True,'ignore_user_config':True,'policy':dict(source='kanban',safe_mode=True,
        ignore_user_config=True,toolsets=[],config_json=json.dumps({'model':{'streaming':streaming}}),kanban_json=json.dumps(context))}
    root=Path(__file__).resolve().parents[2]
    env=dict(os.environ,HOME=str(tmp_path),HERMES_HOME=str(tmp_path),PYTHONPATH=str(root),PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run([sys.executable,'-B','-c','from pathlib import Path\n'+PROGRAM],input=json.dumps(frame),
        cwd=root,env=env,text=True,capture_output=True,timeout=30,check=False)
    assert result.returncode==0,result.stdout+result.stderr
    data=json.loads(result.stdout.splitlines()[-1])
    shapes=data.pop('request_shapes')
    assert len(shapes)==1 and shapes[0]['url']=='http://127.0.0.1:8000/v1/chat/completions'
    assert bool(shapes[0]['stream']) is streaming
    assert data==dict(calls=1,before='running',after='blocked',outcome='text_result_pending_review',
        response='Public bounded answer.',failed=False,interrupted=False,settlement_error=None,extra_refused=True,native_attempts=[]),shapes


def test_restricted_recorders_and_stop_nudge_leave_owner_authority(monkeypatch):
    import logging
    from agent.local_text_policy import local_text_scope
    from agent.kanban_stop import kanban_stop_nudge_enabled,pause_current_kanban_run
    from agent.turn_finalizer import _record_kanban_budget_exhausted,_record_kanban_guardrail_halt
    monkeypatch.setenv('HERMES_KANBAN_TASK','public-fixture')
    monkeypatch.delenv('HERMES_KANBAN_STOP_NUDGE',raising=False)
    def forbidden(*args,**kwargs): pytest.fail('restricted legacy path must not open any board')
    monkeypatch.setattr('hermes_cli.kanban_db_connect.connect',forbidden)
    assert kanban_stop_nudge_enabled()
    with local_text_scope(True):
        assert not kanban_stop_nudge_enabled()
        assert not pause_current_kanban_run()
        _record_kanban_budget_exhausted('public-fixture',1,1,logging.getLogger(__name__))
        _record_kanban_guardrail_halt('public-fixture',None,logging.getLogger(__name__))
    assert kanban_stop_nudge_enabled()
