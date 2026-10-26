"""The final owner receipt, not an optimistic child result, drives text settlement."""
import asyncio
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('interrupted', [False, True])
def test_owner_settles_only_after_finished_and_stop_cause(tmp_path, monkeypatch, interrupted):
    from gateway import session_managed_worker as managed
    from gateway import session_context_refs, session_runtime_workers, session_kanban_text_result
    order = []
    frames = iter([{'type':'hello'}, {'type':'result', 'result':{
        'final_response':'child output', 'completed':True, 'interrupted':interrupted}}, {'type':'finished'}])
    async def next_frame(*args, **kwargs):
        frame = next(frames)
        order.append(frame['type'])
        return frame
    process = SimpleNamespace(wait=lambda timeout: order.append('wait'))
    worker = SimpleNamespace(next_frame=next_frame, stop=threading.Event(), secrets=[],
        writer=SimpleNamespace(start=lambda:None), send=lambda frame:None,
        close=lambda:order.append('close'), stop_cause='owner stop')
    scope = dict(pid=123, birth='birth', secret='secret', session_id='s', execution_id='e')
    authority = SimpleNamespace(_managed_workers={'s':worker}, executing={'s':1}, pending_results={},
        adopt_agent=lambda *args:None, sessions={'s':SimpleNamespace(
            event_stream=SimpleNamespace(lock=threading.RLock()), controls=SimpleNamespace(snapshot=lambda *args:None))})
    async def text(*args): return 'go', None
    async def spawn(*args): return worker, process, None
    async def tracked(authority, fn): return fn()
    monkeypatch.setattr(session_context_refs, 'managed_turn_text', text)
    monkeypatch.setattr(session_runtime_workers, 'tracked_write', tracked)
    monkeypatch.setattr(managed, '_mark_spawning', lambda *args:None)
    monkeypatch.setattr(managed, '_stopped_before_spawn', lambda *args:False)
    monkeypatch.setattr(managed, '_spawn_registered_worker', spawn)
    monkeypatch.setattr(managed, 'reserve_admission_worker', lambda *args, **kwargs:scope)
    monkeypatch.setattr(managed, '_bootstrap', lambda *args:{'api_key':None})
    monkeypatch.setattr(managed, '_prompt_frame', lambda *args:False)
    monkeypatch.setattr(managed, '_settled_session_usage', lambda *args:{})
    def settle(policy, received_scope, result):
        assert received_scope == scope and order[-2:] == ['finished','wait']
        assert result.get('interrupt_message') == ('owner stop' if interrupted else None)
        order.append('settled')
        return {**result, 'final_response':'retained output'}
    monkeypatch.setattr(session_kanban_text_result, 'settle_text_result', settle)
    row = dict(admission_id='a', generation=1, principal_id='p')
    returned = asyncio.run(managed.execute_managed(authority, SimpleNamespace(session_id='s'), row,
                                                  SimpleNamespace(kanban_json='{}')))
    assert returned == 'retained output'
    assert authority.pending_results['a']['result']['final_response'] == returned
    assert order[-2:] == ['settled','close']
