"""Real harmless children exhaust owner bounds without ever importing an agent/provider."""
import asyncio
from dataclasses import replace
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gateway.session_contract import Principal, SessionRef, Submission

CHILD = """import json, os, sys, threading, psutil
me = psutil.Process()
print(json.dumps({'type':'hello', 'pid':me.pid, 'birth':me.create_time(),
                  'ancestors':[p.pid for p in me.parents()[:3]]}), flush=True)
sys.stdin.buffer.readline()
if sys.argv[1] == 'output':
    sys.stderr.write('x' * 4096)
    sys.stderr.flush()
threading.Event().wait()
"""


@pytest.mark.platforms('posix')
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize('mode', ['wall', 'output', 'wall_close_error'])
def test_budget_loss_fences_admission_and_closes_owned_child(tmp_path, monkeypatch, mode):
    from gateway import session_managed_worker as managed
    from gateway.session_authority import LiveSession, SessionAuthority
    from gateway.session_policy import build_policy
    from hermes_state import SessionDB
    from hermes_state_runtime import begin_runtime_epoch, claim_session_input, list_session_admissions

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('s', source='test')
    runner = SimpleNamespace(_draining=False, _cached_agent_for=lambda route: None,
                             _adapter_for_source=lambda source: None)
    authority = SessionAuthority(runner, profile_id=str(tmp_path), instance_id='i', db=db,
                                 epoch=begin_runtime_epoch(db, instance_id='i'))
    authority.sessions['s'] = LiveSession(SimpleNamespace(platform=None, user_id='human', chat_id='c'), 'route')
    actor = Principal('human', str(tmp_path), frozenset({'session:submit', 'session:control'}), 'cli')
    ref = SessionRef(str(tmp_path), 's')
    monkeypatch.setattr(authority, '_schedule', lambda ref: None)
    original = subprocess.Popen
    children = []

    class Child(original):
        def __init__(self, args, **kw):
            super().__init__([sys.executable, '-c', CHILD, mode], **kw)
            children.append(self)
    monkeypatch.setattr(managed.subprocess, 'Popen', Child)
    if mode == 'wall_close_error':
        original_close = managed.ManagedWorker.close
        attempts = []

        def close_with_pipe_error(worker):
            original_close(worker)
            attempts.append(True)
            if len(attempts) == 1:
                raise BrokenPipeError('fixture pipe closed after termination')
        monkeypatch.setattr(managed.ManagedWorker, 'close', close_with_pipe_error)
    from gateway import session_worker_reservation
    original_loss = session_worker_reservation.lose_admission_worker

    def fence_after_close(*args):
        assert children[0].poll() is not None, 'ledger contention must not extend worker execution'
        return original_loss(*args)
    monkeypatch.setattr(session_worker_reservation, 'lose_admission_worker', fence_after_close)
    policy = build_policy({'cwd': str(tmp_path), 'model': 'm'}, {}, profile_terminal=False)
    policy = replace(policy, kanban_json=json.dumps({'execution_budget': {
        'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1}}))

    async def scenario():
        await authority.submit(actor, Submission('turn', ref, {'text': 'go'}, 'queue'))
        await authority.submit(actor, Submission('follower', ref, {'text': 'must remain queued'}, 'queue'))
        row = claim_session_input(db, epoch=authority.epoch, session_id='s')
        with pytest.raises(managed.ManagedExecutionUnknown):
            async with asyncio.timeout(15):
                await managed.execute_managed(authority, ref, row, policy)
        rows = list_session_admissions(db, session_id='s')
        assert [r['status'] for r in rows] == ['unknown', 'queued']
        from hermes_state_runtime import RuntimeStoreError
        with pytest.raises(RuntimeStoreError, match='unknown_execution'):
            claim_session_input(db, epoch=authority.epoch, session_id='s')
        assert not authority.pending_results
    with db:
        asyncio.run(scenario())
    assert len(children) == 1 and children[0].poll() is not None
    log = tmp_path / 'logs' / 'managed-worker.log'
    assert not log.exists() or 'x' * 1024 not in log.read_text()


@pytest.mark.platforms('posix')
def test_spawn_finishing_at_deadline_keeps_cleanup_ownership(tmp_path, monkeypatch):
    from gateway import session_execution_budget, session_managed_worker as managed
    from gateway.session_policy import build_policy
    from hermes_state_runtime import RuntimeStoreError
    budgets, children = [], []
    original_budget = session_execution_budget.ExecutionBudget

    def budget(value):
        instance = original_budget(value)
        budgets.append(instance)
        return instance
    monkeypatch.setattr(session_execution_budget, 'ExecutionBudget', budget)
    original_popen = subprocess.Popen

    class LateChild(original_popen):
        def __init__(self, args, **kw):
            super().__init__([sys.executable, '-c', 'import threading; threading.Event().wait()'], **kw)
            children.append(self)
            # Deterministically cross the deadline exactly as acquisition relinquishes ownership.
            budgets[0].deadline = 0
    monkeypatch.setattr(managed.subprocess, 'Popen', LateChild)
    monkeypatch.setattr(managed, '_worker_env', lambda authority: None)
    authority = SimpleNamespace(profile_id=str(tmp_path), pending_results={},
                                adopt_agent=lambda *args: None)
    row = {'admission_id': 'a', 'generation': 1, 'payload': {'text': 'go'}}
    policy = build_policy({'cwd': str(tmp_path), 'model': 'm'}, {}, profile_terminal=False)
    policy = replace(policy, kanban_json=json.dumps({'execution_budget': {
        'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1}}))
    with pytest.raises(RuntimeStoreError, match='execution_wall_budget'):
        asyncio.run(managed.execute_managed(authority, SessionRef(str(tmp_path), 's'), row, policy))
    assert len(children) == 1 and children[0].poll() is not None
