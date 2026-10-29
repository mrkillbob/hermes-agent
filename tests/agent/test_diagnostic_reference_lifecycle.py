"""Retained diagnostic input must not enter session-end extraction.

Uses real AIAgent lifecycle methods and synthetic agents/provider recorders. No
production issuer, session admission, inference, provider service or Vault is used.
"""
from copy import deepcopy
import threading

import pytest

from agent.diagnostic_reference_policy import DiagnosticPolicy, bind_agent, retained_policy
from agent.local_text_policy import local_text_only, local_text_scope


class RecordingMemory:
    def __init__(self, events, *, fail=False):
        self.events = events
        self.fail = fail
        self.claimed = False

    def claim_teardown(self, *, session_end):
        assert session_end is True
        if self.claimed:
            return False
        self.claimed = True
        return True

    def on_session_end(self, messages):
        self.events.append(('memory', deepcopy(messages)))
        if self.fail:
            raise ValueError('SYNTHETIC_PRIVATE_EXTRACTION_ERROR')

    def shutdown_all(self):
        self.events.append(('provider_shutdown', None))


class RecordingEngine:
    def __init__(self, events):
        self.events = events

    def on_session_end(self, session_id, messages):
        self.events.append(('engine', session_id, deepcopy(messages)))


@pytest.fixture
def lifecycle_agent(monkeypatch):
    from run_agent import AIAgent
    from agent import prepared_compaction

    events = []
    agent = object.__new__(AIAgent)
    agent.session_id = 'synthetic-session'
    agent._memory_manager = RecordingMemory(events)
    agent.context_compressor = RecordingEngine(events)
    agent._session_messages = [
        {'role': 'user', 'content': 'SYNTHETIC_PRIVATE_REFERENCE',
         'api_content': 'SYNTHETIC_PRIVATE_SIDECAR'},
        {'role': 'assistant', 'content': 'SYNTHETIC_PRIVATE_RESPONSE',
         'error': 'SYNTHETIC_PRIVATE_ERROR'},
    ]
    agent._db_flush_scan_prefix = list(agent._session_messages)
    agent._streamed_assistant_text_parts = ['SYNTHETIC_PRIVATE_RESPONSE']
    # No real task resources, clients, DB handles or child processes exist here.
    monkeypatch.setattr(agent, '_close_task_resources', lambda task: events.append(('task_cleanup', task)))
    monkeypatch.setattr(agent, '_close_active_children', lambda **kw: None)
    monkeypatch.setattr(agent, '_close_request_clients', lambda reason: None)
    monkeypatch.setattr(agent, '_trim_process_memory', lambda: None)
    monkeypatch.setattr(prepared_compaction, 'release_for_agent', lambda owner: None)
    return agent, events


def seal_after_scope(agent):
    policy = DiagnosticPolicy('Fixed synthetic host request', '{"schema":"synthetic-ack"}', agent.session_id)
    with local_text_scope(True):
        bind_agent(agent, policy)
        assert local_text_only() is True
    assert local_text_only() is False
    return policy


@pytest.mark.parametrize('method', ['shutdown_memory_provider', 'commit_memory_session', 'close'])
def test_sealed_lifecycle_exports_no_captured_context_after_scope_exit(lifecycle_agent, method):
    agent, events = lifecycle_agent
    policy = seal_after_scope(agent)
    original = deepcopy(agent._session_messages)
    if method == 'close':
        agent.close()
    else:
        getattr(agent, method)(agent._session_messages)
    assert ('memory', []) in events
    assert ('engine', 'synthetic-session', []) in events
    assert 'SYNTHETIC_PRIVATE' not in repr(events)
    assert retained_policy(agent) is policy
    if method != 'close':
        assert agent._session_messages == original
    else:
        assert ('provider_shutdown', None) in events
        assert ('task_cleanup', 'synthetic-session') in events
        assert agent._session_messages == [] and agent._db_flush_scan_prefix is None
        assert agent._streamed_assistant_text_parts == []


def test_context_free_cleanup_thread_uses_retained_seal(lifecycle_agent):
    agent, events = lifecycle_agent
    policy = seal_after_scope(agent)
    outcomes = []

    def cleanup():
        try:
            outcomes.append(local_text_only())
            agent.close()
        except BaseException as exc:
            outcomes.append(type(exc).__name__)

    worker = threading.Thread(target=cleanup)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive() and outcomes == [False]
    assert ('memory', []) in events and ('engine', 'synthetic-session', []) in events
    assert 'SYNTHETIC_PRIVATE' not in repr(events)
    assert retained_policy(agent) is policy


def test_commit_then_repeated_close_preserves_cleanup_and_seal(lifecycle_agent):
    agent, events = lifecycle_agent
    policy = seal_after_scope(agent)
    original = deepcopy(agent._session_messages)
    agent.commit_memory_session(agent._session_messages)
    assert agent._session_messages == original
    agent.close()
    agent.close()
    assert [row for row in events if row[0] == 'memory'] == [('memory', []), ('memory', [])]
    assert [row for row in events if row[0] == 'engine'] == [
        ('engine', 'synthetic-session', []), ('engine', 'synthetic-session', []),
    ]
    assert events.count(('provider_shutdown', None)) == 1
    assert 'SYNTHETIC_PRIVATE' not in repr(events)
    assert retained_policy(agent) is policy


@pytest.mark.parametrize('method', ['shutdown_memory_provider', 'commit_memory_session', 'close'])
def test_unsealed_human_lifecycle_preserves_transcript(lifecycle_agent, method):
    agent, events = lifecycle_agent
    original = deepcopy(agent._session_messages)
    if method == 'close':
        agent.close()
    else:
        getattr(agent, method)(agent._session_messages)
    assert ('memory', original) in events
    assert ('engine', 'synthetic-session', original) in events
    assert retained_policy(agent) is None


def test_sealed_extraction_error_is_withheld_and_cleanup_continues(lifecycle_agent, caplog):
    agent, events = lifecycle_agent
    policy = seal_after_scope(agent)
    agent._memory_manager.fail = True
    agent.close()
    assert 'SYNTHETIC_PRIVATE' not in caplog.text
    assert ('provider_shutdown', None) in events
    assert ('engine', 'synthetic-session', []) in events
    assert ('task_cleanup', 'synthetic-session') in events
    assert retained_policy(agent) is policy


def test_session_id_movement_does_not_unseal_retained_context(lifecycle_agent):
    agent, events = lifecycle_agent
    policy = seal_after_scope(agent)
    agent.session_id = 'synthetic-replacement'
    agent.commit_memory_session(agent._session_messages)
    assert ('memory', []) in events
    assert ('engine', 'synthetic-replacement', []) in events
    assert retained_policy(agent) is policy
