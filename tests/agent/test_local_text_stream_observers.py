"""Restricted stream payloads never reach plugin lookup, queues or callback threads."""
from concurrent.futures import ThreadPoolExecutor
import contextvars
import os
import threading

import pytest

from agent import plugin_stream_hooks as stream, safe_worker_policy
from agent.local_text_policy import local_text_scope


@pytest.mark.parametrize('kind', ['scope', 'worker', 'safe_worker'])
@pytest.mark.parametrize('operation', ['enqueue', 'has', 'reasoning'])
def test_restricted_stream_refuses_before_plugin_or_config_lookup(kind, operation, monkeypatch):
    from hermes_cli import config, plugins
    touched = []
    def callbacks(name):
        touched.append(('lookup', name))
        return ()
    monkeypatch.setattr(plugins, 'iter_hook_callbacks', callbacks)
    monkeypatch.setattr(config, 'load_config_readonly', lambda: touched.append(('config',)) or {})
    if kind in ('worker', 'safe_worker'):
        monkeypatch.setattr(safe_worker_policy, '_policy',
            safe_worker_policy._SafeWorkerPolicy(os.getpid(), True, True, '{}', kind == 'worker'))
    with local_text_scope(kind == 'scope'):
        if operation == 'enqueue':
            result = stream.enqueue_plugin_stream_hook('on_stream_delta', delta='private-reference-fixture')
        elif operation == 'has':
            result = stream.has_stream_observer_hooks()
        else:
            result = stream.has_reasoning_stream_observer_hooks()
    assert result is False
    assert touched == []


def test_scoped_refusal_precedes_context_capture_or_dispatcher_access(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('restricted payload capture or dispatcher access started')
    with local_text_scope(True):
        with monkeypatch.context() as patch:
            patch.setattr(stream.contextvars, 'copy_context', forbidden)
            patch.setattr(stream, '_dispatchers_for', forbidden)
            result = stream.enqueue_plugin_stream_hook('on_stream_delta', delta='private-reference-fixture')
    assert result is False


def test_copied_restriction_and_worker_snapshot_cover_thread_dispatch(monkeypatch):
    from hermes_cli import plugins
    looked_up = []
    monkeypatch.setattr(plugins, 'iter_hook_callbacks', lambda name: looked_up.append(name) or ())
    with ThreadPoolExecutor(1) as executor:
        with local_text_scope(True):
            context = contextvars.copy_context()
            assert executor.submit(context.run, stream.enqueue_plugin_stream_hook,
                                   'on_stream_delta', delta='private-reference-fixture').result(timeout=5) is False
        assert looked_up == []
        monkeypatch.setattr(safe_worker_policy, '_policy',
            safe_worker_policy._SafeWorkerPolicy(os.getpid(), True, True, '{}', True))
        assert executor.submit(stream.enqueue_plugin_stream_hook, 'on_stream_delta',
                               delta='private-reference-fixture').result(timeout=5) is False
    assert looked_up == []


def test_ordinary_dispatch_after_scope_reaches_actual_callback(monkeypatch):
    from hermes_cli import plugins
    delivered, done = [], threading.Event()
    def callback(**payload):
        delivered.append(payload['delta'])
        done.set()
    monkeypatch.setattr(plugins, 'iter_hook_callbacks', lambda name: (callback,))
    try:
        with local_text_scope(True):
            assert stream.enqueue_plugin_stream_hook('on_stream_delta', delta='private-reference-fixture') is False
        assert delivered == []
        assert stream.enqueue_plugin_stream_hook('on_stream_delta', delta='ordinary') is True
        assert done.wait(timeout=5)
        assert delivered == ['ordinary']
    finally:
        stream.shutdown_plugin_stream_hook_dispatcher()
