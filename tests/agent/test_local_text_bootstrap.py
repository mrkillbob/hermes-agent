"""Fresh private bootstrap binds restrictions before runtime imports, for raw threads too."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROGRAM = '''
import json, sys
from concurrent.futures import ThreadPoolExecutor
from agent.managed_worker import bind_worker_policy
from agent.local_text_policy import local_text_only, local_text_scope
frame = json.load(sys.stdin)
try:
    bind_worker_policy(frame)
except ValueError:
    print(json.dumps({'refused': True, 'bound': local_text_only()}))
else:
    with ThreadPoolExecutor(1) as pool:
        observed = pool.submit(local_text_only).result()
    with local_text_scope(False):
        weakened = local_text_only()
    try:
        bind_worker_policy(frame)
    except RuntimeError:
        rebound = False
    else:
        rebound = True
    print(json.dumps({'refused':False,'raw_thread':observed,'weakened':weakened,'rebound':rebound}))
'''


def frame(restricted):
    context = dict(execution_budget=dict(wall_seconds=60,output_bytes=8192,requests=1,local_text_only=True),
        route=dict(provider='llamacpp',model='public-model',reasoning='none',base_url='http://127.0.0.1:8000/v1'))
    return {'safe_mode':restricted,'ignore_user_config':restricted,'policy':dict(source='kanban' if restricted else 'cli',
        safe_mode=restricted,ignore_user_config=restricted,toolsets=[],config_json='{}',
        kanban_json=json.dumps(context) if restricted else None)}


def probe(value, tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, HERMES_HOME=str(tmp_path), HOME=str(tmp_path),
               PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(root))
    result = subprocess.run([sys.executable,'-B','-c',PROGRAM],input=json.dumps(value),
        cwd=root,env=env,text=True,capture_output=True,timeout=15,check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('restricted',[False,True])
def test_private_snapshot_covers_raw_threads_without_weakening(tmp_path, restricted):
    result = probe(frame(restricted),tmp_path)
    assert result == dict(refused=False,raw_thread=restricted,weakened=restricted,rebound=False)


@pytest.mark.parametrize('field',['toolsets','safe_mode','ignore_user_config','frame_safe_mode'])
def test_inconsistent_worker_frame_refuses_before_binding(tmp_path,field):
    value = frame(True)
    if field == 'frame_safe_mode': value['safe_mode'] = False
    else: value['policy'][field] = ['terminal'] if field == 'toolsets' else False
    assert probe(value,tmp_path) == {'refused':True,'bound':False}
