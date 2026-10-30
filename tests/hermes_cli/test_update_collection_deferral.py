"""Paused first-hop policy crosses actual fresh completion interpreters.

Only machine-boundary ports are replaced: PM sync/tools, build/maintenance,
collectors and services. No environment is built and no installed tree is read.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROGRAM = r'''
import contextvars, hashlib, importlib.util, json, os, subprocess, sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
source, cell, mode, phase = sys.argv[1:]
cell = Path(cell)
request_file, result_file = cell/'request.json', cell/'result.json'
request = json.loads(request_file.read_text())
def event(name):
    with (cell/'events.jsonl').open('a') as stream:
        stream.write(json.dumps({'name':name,'pid':os.getpid()})+'\n')
for name in ('pm','pm.client','pm.receipt','pm.environments','hermes_cli',
             'hermes_cli.venv_sync','hermes_cli.gitlock','hermes_cli.update_lock',
             'hermes_cli.main','hermes_cli.update_cmd','hermes_cli.update_cmd_config',
             'hermes_cli.source_completion','hermes_cli.update_inventory',
             'hermes_cli.update_receipt'):
    module = ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
    if '.' in name:
        parent, attr = name.rsplit('.',1)
        setattr(sys.modules[parent],attr,module)
def module(name): return sys.modules[name]
def sync(**kw):
    event('fixture-sync')
    if mode == 'prepare-error': raise RuntimeError('FIXTURE_SYNC_REFUSED')
module('pm').sync_venv = sync
module('pm.client').ensure_tools_for_sync = lambda: event('fixture-tools')
pr = module('pm.receipt')
pr.worker_context = lambda ident: nullcontext()
pr.last_for_update = lambda ident: None
pr.accept_worker_receipt = lambda *args: None
environments = module('pm.environments')
environments.project_python = lambda root: Path(sys.executable)
environments.activation_environment = lambda root: {'PATH':os.defpath,'HOME':str(cell),'HERMES_HOME':str(cell/'home')}
ports = module('hermes_cli.venv_sync')
ports.arm_completion = lambda root: event('arm')
ports.clear_completion = lambda root: event('clear')
def collector(root):
    event('collector')
    if request.get('defer_collection') is True: raise AssertionError('COLLECTION_FORBIDDEN')
ports.collect_superseded_generations = collector
module('hermes_cli.gitlock').convert_treeless_checkout_first = lambda root: None
module('hermes_cli.update_lock').checkout_lock_fds = lambda root: ()
module('hermes_cli.update_lock')._acquire_checkout = lambda root: None
inventory = module('hermes_cli.update_inventory')
inventory.RuntimeRecord = inventory.UpdatePlan = lambda **kw: None
receipts = module('hermes_cli.update_receipt')
receipts._current = contextvars.ContextVar('receipt', default=None)
receipts.UpdateReceipt = type('UpdateReceipt',(),{})
receipts.TAIL_FOLLOWUPS = frozenset()
receipts.record_user_action = lambda *args: None
receipts.record_build_stage = lambda *args: None
receipts.record_followup = lambda *args, **kwargs: event('followup')
receipts.record_stage = lambda *args: None
receipts.record_skip = lambda *args: event('restart-deferred')
def finalize(code, reason):
    if request['receipt'].get('finished_at'): return
    request['receipt'].update(finished_at='fixture',outcome='success' if code == 0 else 'failed')
receipts.finalize_pending_update_receipt = finalize
def interrupted(reason, exit_code):
    request['receipt'].update(finished_at='fixture',outcome='interrupted')
receipts.finalize_interrupted_update_receipt = interrupted
receipts.read_run_record = lambda ident: (None,request['receipt']) if request['receipt'].get('finished_at') else None
receipts.finalized_receipt = lambda ident: None
receipts.owe_followup = lambda *args: event('followup')
cmd = module('hermes_cli.update_cmd')
cmd._sweep_bytecode_after_update = lambda branch: None
cmd._write_gateway_update_exit_code = lambda ok: None
def resume(token):
    if token and token.get('resume_needed'): event('RESUME_FORBIDDEN')
cmd._resume_windows_gateways_after_update = resume
cmd._m = lambda: cmd
cmd._fleet_restart_skip_reason = lambda plan: None
cmd._pending_fleet_restart_needed = lambda: False
cmd._restart_gateway_fleet_after_update = lambda *args: event('RESTART_FORBIDDEN')
def complete(root, **kw):
    kw['before_build']()
    if mode == 'interrupt': raise KeyboardInterrupt()
    event('fixture-build-maintenance')
    return True
module('hermes_cli.source_completion').complete_source_checkout = complete
path = Path(source)/'hermes_cli/update_completion.py'
digest = hashlib.sha256(path.read_bytes()).hexdigest()
assert digest == request['module_sha256']
assert 'hermes_cli.update_completion' not in sys.modules
spec = importlib.util.spec_from_file_location('hermes_cli.update_completion',path)
completion = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = completion
spec.loader.exec_module(completion)
event('fresh-'+phase)
pause = cell/'home/ESTOP'
before = pause.read_bytes()
if phase == 'prepare':
    def prepared_child(command, **kw):
        assert '--prepared' in command and kw['env']['HERMES_HOME'] == str(cell/'home')
        return subprocess.run([sys.executable,'-I','-S','-B','-c',PROGRAM,source,str(cell),mode,'prepared'],
                              env=kw['env'],cwd=cell,timeout=15,check=False).returncode
    # The only replaced process port launches another actual isolated interpreter.
    PROGRAM = os.environ['FIXTURE_PROGRAM']
    completion.subprocess.call = prepared_child
    code = completion._bootstrap(request,request_file,result_file)
else:
    assert request['no_gateway_restart'] is True and request['windows_resume'] is None
    code = completion._finish(request,result_file)
assert pause.read_bytes() == before
raise SystemExit(code)
'''


@pytest.mark.parametrize(('defer', 'fault'), [(True, 'none'), (False, 'none'), (1, 'none'), ('true', 'none'), (None, 'none'), (True, 'prepare-error'), (True, 'interrupt')])
def test_exact_collection_policy_crosses_fresh_completion_processes(tmp_path, defer, fault):
    source = Path(__file__).resolve().parents[2]
    import hashlib

    home = tmp_path / 'home'
    home.mkdir()
    pause = home / 'ESTOP'
    pause.write_bytes(b'operator pause\n')
    (home / 'cron.json').write_bytes(b'{"enabled":true}')
    (home / 'config.yaml').write_bytes(b'kanban: {dispatch_in_gateway: false}\n')
    held = {p: (p.stat().st_ino, p.read_bytes()) for p in home.iterdir()}
    request = {
        'source': str(tmp_path / 'fixture-source'), 'home': str(home),
        'receipt': {'update_id': 'fixture'}, 'defer_collection': defer,
        'module_sha256': hashlib.sha256((source / 'hermes_cli/update_completion.py').read_bytes()).hexdigest(),
        'bytecode_cache': str(tmp_path / 'bytecode'), 'no_gateway_restart': True,
        'windows_resume': None, 'sibling_snapshots': {}, 'plan': None,
        'branch': 'fixture', 'desktop': False, 'assume_yes': True,
        'gateway_mode': False, 'snapshot_id': None, 'pre_update_version': None,
    }
    if defer is None:
        request.pop('defer_collection')
    (tmp_path / 'request.json').write_text(json.dumps(request))
    done = subprocess.run(
        [sys.executable, '-I', '-S', '-B', '-c', PROGRAM, str(source), str(tmp_path),
         fault, 'prepare'],
        env={'PATH': os.defpath, 'HOME': str(tmp_path), 'HERMES_HOME': str(home), 'FIXTURE_PROGRAM': PROGRAM},
        cwd=tmp_path, capture_output=True, text=True, timeout=35, check=False,
    )
    assert done.returncode == (130 if fault == 'interrupt' else 0), done.stdout + done.stderr
    events = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    names = [event['name'] for event in events]
    assert names.count('collector') == (0 if defer is True else 1)
    assert {'fixture-sync', 'fixture-tools'} <= set(names)
    if fault == 'none':
        assert {'fixture-build-maintenance', 'restart-deferred'} <= set(names)
    elif fault == 'prepare-error':
        assert 'followup' in names and 'fresh-prepared' not in names
    assert 'RESUME_FORBIDDEN' not in names and 'RESTART_FORBIDDEN' not in names
    if fault != 'prepare-error':
        assert next(e['pid'] for e in events if e['name'] == 'fresh-prepare') != next(
            e['pid'] for e in events if e['name'] == 'fresh-prepared')
    assert json.loads((tmp_path / 'result.json').read_text())['receipt']['outcome'] == ('interrupted' if fault == 'interrupt' else 'success')
    assert {p: (p.stat().st_ino, p.read_bytes()) for p in held} == held
