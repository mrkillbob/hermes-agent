"""Restricted construction must not contact a provider or start a toolchain probe."""
import json
import os
from pathlib import Path
import subprocess
import sys

PROGRAM = '''
import json, socket, subprocess, sys
from agent.managed_worker import bind_worker_policy
frame = json.load(sys.stdin)
bind_worker_policy(frame)
from hermes_cli.version_info import get_version_info
get_version_info()  # Core source identity is read before construction; no model/tool work.
attempts = []
def forbidden_socket(*args, **kwargs):
    attempts.append('socket')
    raise AssertionError('constructor socket forbidden')
def forbidden_process(*args, **kwargs):
    attempts.append('process')
    raise AssertionError('constructor subprocess forbidden')
socket.socket.connect = forbidden_socket
subprocess.Popen = forbidden_process
import httpx, run_agent
from openai import OpenAI
def http_request(request):
    attempts.append('http')
    raise AssertionError('constructor HTTP forbidden')
def client(*args, **kwargs):
    kwargs['http_client'] = httpx.Client(transport=httpx.MockTransport(http_request))
    return OpenAI(*args, **kwargs)
run_agent.OpenAI = client
agent = run_agent.AIAgent(model='public-model', provider='llamacpp',
    api_key='no-key-required', base_url='http://127.0.0.1:8000/v1', api_mode='chat_completions',
    enabled_toolsets=[], fallback_model={}, max_iterations=1, quiet_mode=True,
    skip_context_files=True, skip_memory=True, skip_background_review=True, load_soul_identity=False)
print(json.dumps({'attempts':attempts,'tools':agent.tools,'fallback':agent._fallback_chain,
    'probe':agent._environment_probe,'model':agent.model}))
'''


def test_actual_constructor_uses_no_ambient_network_or_process(tmp_path):
    root = Path(__file__).resolve().parents[2]
    context = {'execution_budget':dict(wall_seconds=60,output_bytes=8192,requests=1,local_text_only=True),
               'route':dict(provider='llamacpp',model='public-model',reasoning='none',base_url='http://127.0.0.1:8000/v1')}
    frame = {'safe_mode':True,'ignore_user_config':True,'policy':dict(source='kanban',safe_mode=True,
        ignore_user_config=True,toolsets=[],config_json='{}',kanban_json=json.dumps(context))}
    env = dict(os.environ,HOME=str(tmp_path),HERMES_HOME=str(tmp_path),PYTHONPATH=str(root),PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable,'-B','-c',PROGRAM],input=json.dumps(frame),
        cwd=root,env=env,text=True,capture_output=True,timeout=30,check=False)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.splitlines()[-1])
    assert data == dict(attempts=[],tools=[],fallback=[],probe=False,model='public-model')
