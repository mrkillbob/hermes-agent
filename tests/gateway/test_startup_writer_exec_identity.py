"""The actual reserved POSIX re-exec remains visible to canonical identity readers."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gateway import status
from hermes_cli.update_cmd_windows import _hermes_holder_subcommand
from pm.lifetime_launch import run

pytestmark = pytest.mark.platforms('posix')
ROOT = Path(__file__).resolve().parents[2]
DRIVER = r'''
import base64,json,os,sys
from pathlib import Path
from hermes_cli import _launchers,startup_hold, startup_hold_exec,venv_sync
root=Path.cwd(); argv=json.loads(os.environ['IDENTITY_ARGV']); form=os.environ['IDENTITY_FORM']
sys.argv=['hermes',*argv]
startup_hold.register_process_writer(argv)
original=[sys.executable,'-X','utf8','-m','hermes_cli.main',*argv]
logical=[str(root/'hermes_cli/main.py'),*argv]; module='hermes_cli.main'
if form=='path': module=None
elif form in ('launcher','base64-launcher'):
    source=_launchers._launcher_script('hermes',root,None)
    if form=='base64-launcher':
        source="import base64; exec(base64.b64decode('"+base64.b64encode(source.encode()).decode()+"'))"
    logical=['-c',*argv]; original=[sys.executable,'-X','utf8','-c',source,*argv]; module=None
for _ in range(int(os.environ['IDENTITY_REPEATS'])):
    command=venv_sync.relaunch_command(Path(sys.executable),root,logical,original,module)
    body=command[-1]
    with startup_hold_exec.writer_exec_handoff(command) as wrapped:
        captured=wrapped
    # This is the logical invocation the actual exec suite verifies the prelude restores.
    original=[*command[:-1],body]
print(json.dumps(captured))
'''


def emitted(tmp_path, argv, form='module', repeats=1):
    home=tmp_path/'home'; home.mkdir(exist_ok=True)
    result=run([sys.executable,'-c',DRIVER],cwd=ROOT,
               env=dict(os.environ,HERMES_HOME=str(home),IDENTITY_ARGV=json.dumps(argv),IDENTITY_FORM=form,IDENTITY_REPEATS=str(repeats)),
               capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Exception ignored in atexit' not in result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('repeats',[1,2,3])
@pytest.mark.parametrize('form',['module','path','launcher','base64-launcher'])
@pytest.mark.parametrize('join',['space','windows'])
@pytest.mark.parametrize('argv,holder,profile',[
    (['gateway','run'],'gateway',None),
    (['--profile','Work','gateway','run'],'gateway','work'),
    (['serve','-p','Work'],'serve','work'),
    (['dashboard','-p','Work'],'dashboard','work'),
])
def test_reserved_reexec_keeps_gateway_holder_and_profile_identity(tmp_path,form,join,argv,holder,profile,repeats):
    command=emitted(tmp_path,argv,form,repeats)
    line=' '.join(command) if join=='space' else subprocess.list2cmdline(command)
    assert status.looks_like_gateway_command_line(line) is (holder=='gateway')
    assert _hermes_holder_subcommand(line)==holder
    assert status.profile_flag_value(line)==profile


_MUTATIONS = {
    'quoted': repr,
    'dead': lambda source: 'if False:\n'+''.join('    '+line+'\n' for line in source.splitlines()),
    'extra-statement': lambda source: source.replace(
        'del _hermes_restore_startup_writer\n','del _hermes_restore_startup_writer\nprint(0)\n'),
    'bad-terminal': lambda source: source.replace('del _hermes_restore_startup_writer\n',''),
    'changed-body': lambda source: source.replace('sys.path.insert(0,','sys.path.insert(1,'),
    'bad-schema': lambda source: source.replace("'schema': 1", "'schema': True"),
    'extra-field': lambda source: source.replace("'state': 'exec_pending'", "'state': 'exec_pending', 'extra': 1"),
}


@pytest.mark.parametrize('kind',['carrier',*_MUTATIONS])
def test_reserved_reexec_identity_rejects_nonexecuting_and_changed_envelopes(tmp_path,kind):
    command=emitted(tmp_path,['gateway','run'])
    source=command[-1]
    prefix="import base64; exec(base64.b64decode('"
    if source.startswith(prefix) and source.endswith("'))"):
        source=base64.b64decode(source[len(prefix):-3],validate=True).decode()
    if kind=='carrier':
        command=[sys.executable,'-c','import time; command = []','1234',*command]
    else:
        source=_MUTATIONS[kind](source)
        command[-1]=prefix+base64.b64encode(source.encode()).decode()+"'))"
    line=' '.join(command)
    assert not status.looks_like_gateway_command_line(line)
    assert _hermes_holder_subcommand(line) is None


def test_reserved_reexec_identity_bounds_nested_encoded_carriers(tmp_path):
    command=emitted(tmp_path,['gateway','run'])
    for _ in range(16):
        command[-1]="import base64; exec(base64.b64decode('"+base64.b64encode(command[-1].encode()).decode()+"'))"
    assert not status.looks_like_gateway_command_line(' '.join(command))
    assert _hermes_holder_subcommand(' '.join(command)) is None
