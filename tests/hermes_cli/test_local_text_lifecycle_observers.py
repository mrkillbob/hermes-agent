"""Restricted turns do not export payloads to lifecycle observers or plugins."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os

import pytest

from agent import relay_runtime, safe_worker_policy
from agent.local_text_policy import local_text_scope
from hermes_cli import lifecycle, observability, plugins


@contextmanager
def _restriction(kind, monkeypatch):
    if kind in ('worker', 'safe_worker'):
        policy = safe_worker_policy._SafeWorkerPolicy(os.getpid(), True, True, '{}', kind == 'worker')
        monkeypatch.setattr(safe_worker_policy, '_policy', policy)
    with local_text_scope(kind == 'scope'):
        yield


@pytest.mark.parametrize('kind', ['scope', 'worker', 'safe_worker'])
@pytest.mark.parametrize('operation', ['invoke', 'async', 'has', 'finalize'])
def test_restricted_dispatch_withholds_payload_but_preserves_core_close(kind, operation, monkeypatch):
    observed, closed = [], []
    def record(name, **kwargs):
        observed.append((name, kwargs))
        return ['observer-result']
    async def async_record(name, **kwargs):
        return record(name, **kwargs)
    monkeypatch.setattr(observability, 'observe_lifecycle', record)
    monkeypatch.setattr(observability, 'handles_hook', lambda name: observed.append(name) or True)
    monkeypatch.setattr(plugins, 'invoke_hook', record)
    monkeypatch.setattr(plugins, 'ainvoke_hook', async_record)
    monkeypatch.setattr(plugins, 'has_hook', lambda name: observed.append(name) or True)
    monkeypatch.setattr(relay_runtime.SESSION_COORDINATOR, 'finalize_conversation', lambda **kw: closed.append(kw))
    monkeypatch.setattr(relay_runtime, 'current_profile_key', lambda: 'fixture-profile')
    payload = {'session_id': 'fixture-session', 'messages': [{'content': 'private-reference-fixture'}]}
    with _restriction(kind, monkeypatch):
        if operation == 'invoke':
            result = lifecycle.invoke_hook('pre_api_request', **payload)
        elif operation == 'async':
            result = asyncio.run(lifecycle.ainvoke_hook('pre_api_request', **payload))
        elif operation == 'has':
            result = lifecycle.has_hook('pre_api_request')
        else:
            result = lifecycle.finalize_session(**payload)
    assert result == (False if operation == 'has' else [])
    assert observed == []
    assert closed == ([{'profile_key': 'fixture-profile', 'session_id': 'fixture-session'}]
                      if operation == 'finalize' else [])
    assert payload['messages'][0]['content'] == 'private-reference-fixture'


def test_worker_restriction_covers_context_free_dispatch(monkeypatch):
    observed = []
    async def callback(*args, **kwargs):
        observed.append(kwargs)
        return ['unexpected']
    monkeypatch.setattr(plugins, 'ainvoke_hook', callback)
    with _restriction('worker', monkeypatch):
        with ThreadPoolExecutor(1) as executor:
            result = executor.submit(lambda: asyncio.run(lifecycle.ainvoke_hook('post_api_request',
                                     response='private-reference-fixture'))).result(timeout=5)
    assert result == []
    assert observed == []


def test_scoped_refusal_resets_without_suppressing_ordinary_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(observability, 'observe_lifecycle', lambda name, **kw: calls.append('builtin'))
    monkeypatch.setattr(plugins, 'invoke_hook', lambda name, **kw: calls.append('plugin') or ['ok'])
    with pytest.raises(LookupError):
        with local_text_scope(True):
            with local_text_scope(False):
                assert lifecycle.invoke_hook('on_session_end', messages=['private-reference-fixture']) == []
            raise LookupError('fixture')
    assert calls == []
    assert lifecycle.invoke_hook('on_session_end', messages=['ordinary']) == ['ok']
    assert calls == ['builtin', 'plugin']
