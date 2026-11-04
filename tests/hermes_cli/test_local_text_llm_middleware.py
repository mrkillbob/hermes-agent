"""Restricted LLM calls keep payloads out of plugins, without suppressing execution."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import os
from types import SimpleNamespace

import pytest

from agent import safe_worker_policy
from agent.local_text_policy import local_text_scope
from hermes_cli import middleware, plugins


@contextmanager
def restriction(kind, monkeypatch):
    if kind != 'scope':
        policy = safe_worker_policy._SafeWorkerPolicy(os.getpid(), True, True, '{}', kind == 'worker')
        monkeypatch.setattr(safe_worker_policy, '_policy', policy)
    with local_text_scope(kind == 'scope'):
        yield


@pytest.fixture
def sinks(monkeypatch):
    seen = []

    def discover(name):
        seen.append(('discover', name))
        return True

    def request(name, **kwargs):
        seen.append(('request', kwargs['request']))
        return [{'request': {'messages': ['rewritten']}}]

    def execution(**kwargs):
        seen.append(('execution', kwargs['request']))
        result = kwargs['next_call']()
        seen.append(('response', result))
        return result

    def manager():
        seen.append(('manager', None))
        return SimpleNamespace(_middleware={'llm_execution': [execution]})

    monkeypatch.setattr(plugins, 'has_middleware', discover)
    monkeypatch.setattr(plugins, 'invoke_middleware', request)
    monkeypatch.setattr(plugins, '_delivery_manager', manager)
    return seen


def execute(request):
    return middleware.run_llm_execution_middleware(request, lambda value: {'received': value})


@pytest.mark.parametrize('kind', ['scope', 'worker', 'safe_worker'])
@pytest.mark.parametrize('phase', ['request', 'execution'])
def test_restricted_payload_bypasses_plugins_but_reaches_terminal(kind, phase, sinks, monkeypatch):
    payload = {'messages': [{'role': 'user', 'content': 'PRIVATE-INERT-REFERENCE'}]}
    with restriction(kind, monkeypatch):
        if phase == 'request':
            result = middleware.apply_llm_request_middleware(payload)
            assert result.payload is payload and result.original_payload is payload
            assert result.changed is False and result.trace == []
        else:
            assert execute(payload)['received'] is payload
    assert sinks == []


def test_ordinary_middleware_still_rewrites_and_observes_response(sinks):
    result = middleware.apply_llm_request_middleware({'messages': ['ordinary']})
    assert result.payload == {'messages': ['rewritten']}
    assert execute(result.payload) == {'received': result.payload}
    assert [name for name, _ in sinks] == ['discover', 'request', 'manager', 'execution', 'response']


def test_restricted_terminal_error_propagates_once_and_scope_resets(sinks):
    calls = []
    error = LookupError('PRIVATE-INERT-ERROR')

    def terminal(request):
        calls.append(request)
        raise error

    with pytest.raises(LookupError) as caught:
        with local_text_scope(True):
            with local_text_scope(False):
                middleware.run_llm_execution_middleware({'messages': ['private']}, terminal)
    assert caught.value is error
    assert calls == [{'messages': ['private']}] and sinks == []
    execute({'messages': ['ordinary']})
    assert [name for name, _ in sinks] == ['manager', 'execution', 'response']


@pytest.mark.parametrize('kind', ['scope', 'worker'])
def test_restriction_reaches_its_supported_thread_domain(kind, sinks, monkeypatch):
    with restriction(kind, monkeypatch):
        with ThreadPoolExecutor(1) as pool:
            # Context scopes explicitly propagate; process worker policy covers empty contexts.
            call = lambda: execute({'messages': ['PRIVATE-INERT-THREAD']})
            result = pool.submit(copy_context().run, call).result(5) if kind == 'scope' else pool.submit(call).result(5)
    assert result == {'received': {'messages': ['PRIVATE-INERT-THREAD']}}
    assert sinks == []
