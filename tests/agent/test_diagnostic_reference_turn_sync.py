"""Retained diagnostic context must not be mirrored by completed-turn memory sync.

Real agent/manager methods use synthetic data and a recording provider. The queue
boundary is captured deterministically: no service, worker pool or model is used.
"""
from copy import deepcopy
import threading

import pytest

from agent.diagnostic_reference_policy import DiagnosticPolicy, bind_agent, retained_policy
from agent.local_text_policy import local_text_only, local_text_scope


class RecordingManager:
    def __init__(self):
        self.calls = []

    def sync_all(self, user, response, **kwargs):
        self.calls.append(('sync', user, response, kwargs))

    def queue_prefetch_all(self, query, **kwargs):
        self.calls.append(('prefetch', query, kwargs))


@pytest.fixture
def turn_agent():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent.session_id = 'synthetic-session'
    agent._memory_manager = RecordingManager()
    agent._turn_author = {'kind': 'human', 'origin': 'synthetic-owner'}
    messages = [
        {'role': 'user', 'content': 'Synthetic reference about blue widgets',
         'api_content': 'Synthetic private API sidecar', 'message_uid': 'synthetic-note-id'},
        {'role': 'assistant', 'content': 'Synthetic reference response',
         'error': 'Synthetic private error', 'source': '[[SyntheticDecision]]'},
    ]
    agent._session_messages = messages
    return agent, messages


def seal(agent):
    policy = DiagnosticPolicy('Fixed host request', '{"schema":"synthetic-ack"}', agent.session_id)
    with local_text_scope(True):
        bind_agent(agent, policy)
    assert local_text_only() is False
    return policy


def sync(agent, messages, **overrides):
    values = {'original_user_message': 'Synthetic reference about blue widgets',
              'final_response': 'Synthetic reference response', 'interrupted': False,
              'messages': messages}
    agent._sync_external_memory_for_turn(**{**values, **overrides})


@pytest.mark.parametrize('scoped', [True, False], ids=['in-scope', 'after-scope'])
def test_sealed_turn_never_syncs_or_prefetches(turn_agent, scoped):
    agent, messages = turn_agent
    policy = seal(agent)
    original = deepcopy(messages)
    with local_text_scope(scoped):
        sync(agent, messages)
    assert agent._memory_manager.calls == []
    assert messages == original and agent._session_messages is messages
    assert retained_policy(agent) is policy


def test_context_free_thread_still_honors_retained_seal(turn_agent):
    agent, messages = turn_agent
    policy = seal(agent)
    outcomes = []

    def worker():
        try:
            outcomes.append(local_text_only())
            sync(agent, messages)
        except BaseException as exc:
            outcomes.append(type(exc).__name__)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive() and outcomes == [False]
    assert agent._memory_manager.calls == []
    assert retained_policy(agent) is policy


@pytest.mark.parametrize('boundary', ['manager', 'formatting'])
def test_sealed_refusal_precedes_manager_query_and_input_formatting(turn_agent, monkeypatch, boundary):
    import run_agent

    agent, messages = turn_agent
    policy = seal(agent)
    if boundary == 'manager':
        class UnvisitedManager(RecordingManager):
            def __bool__(self):
                pytest.fail('sealed input must be refused before querying the manager')
        agent._memory_manager = UnvisitedManager()
    else:
        def unexpected_format(*args, **kwargs):
            pytest.fail('sealed input must not reach message formatting')
        monkeypatch.setattr(run_agent, '_summarize_user_message_for_log', unexpected_format)
    sync(agent, messages)
    assert agent._memory_manager.calls == []
    assert retained_policy(agent) is policy


def test_session_id_change_does_not_unseal_turn_sync(turn_agent):
    agent, messages = turn_agent
    policy = seal(agent)
    agent.session_id = 'synthetic-replacement'
    sync(agent, messages)
    assert agent._memory_manager.calls == []
    assert retained_policy(agent) is policy


def test_ordinary_human_turn_preserves_transcript_author_and_prefetch(turn_agent):
    agent, messages = turn_agent
    original = deepcopy(messages)
    sync(agent, messages)
    assert [row[0] for row in agent._memory_manager.calls] == ['sync', 'prefetch']
    row = agent._memory_manager.calls[0]
    assert row[1:3] == ('Synthetic reference about blue widgets', 'Synthetic reference response')
    assert row[3]['messages'] is messages
    assert row[3]['turn_author'] is agent._turn_author
    assert row[3]['session_id'] == 'synthetic-session'
    assert agent._memory_manager.calls[1] == (
        'prefetch', 'Synthetic reference about blue widgets', {'session_id': 'synthetic-session'})
    assert messages == original and retained_policy(agent) is None


def test_ordinary_trivial_turn_syncs_without_prefetch(turn_agent):
    agent, messages = turn_agent
    sync(agent, messages, original_user_message='hi')
    assert [row[0] for row in agent._memory_manager.calls] == ['sync']
    assert agent._memory_manager.calls[0][1] == 'hi'


@pytest.mark.parametrize('reason', ['interrupted', 'empty-user', 'empty-response', 'no-manager'])
def test_ordinary_existing_skip_conditions_are_preserved(turn_agent, reason):
    agent, messages = turn_agent
    manager = agent._memory_manager
    overrides = {'interrupted': True} if reason == 'interrupted' else (
        {'original_user_message': ''} if reason == 'empty-user' else (
            {'final_response': ''} if reason == 'empty-response' else {}))
    if reason == 'no-manager':
        agent._memory_manager = None
    sync(agent, messages, **overrides)
    assert manager.calls == []


class RecordingProvider:
    name = 'synthetic-recording-provider'

    def __init__(self):
        self.calls = []

    def sync_turn(self, user, response, **kwargs):
        self.calls.append(('sync', user, response, kwargs))

    def queue_prefetch(self, query, **kwargs):
        self.calls.append(('prefetch', query, kwargs))


@pytest.fixture
def provider_lane(turn_agent, monkeypatch, tmp_path):
    from agent.memory_caller_scope import bound_scope
    from agent.memory_manager import MemoryManager
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(tmp_path))
    try:
        agent, messages = turn_agent
        provider = RecordingProvider()
        manager = MemoryManager()
        # Supply only the external backend/queue boundary; keep real profile,
        # scaffold stripping, egress redaction, signature and fan-out behavior.
        manager._providers = [provider]
        manager._bound_scope = bound_scope(str(tmp_path), 'synthetic-agent')
        queued = []
        monkeypatch.setattr(manager, '_submit_background', lambda call, **kw: queued.append(call))
        agent._memory_manager = manager
        yield agent, messages, provider, queued
    finally:
        reset_hermes_home_override(token)


def test_real_manager_queues_no_sealed_payload(provider_lane):
    agent, messages, provider, queued = provider_lane
    policy = seal(agent)
    original = deepcopy(messages)
    sync(agent, messages)
    assert queued == [] and provider.calls == []
    assert messages == original and retained_policy(agent) is policy


def test_real_manager_ordinary_positive_control_delivers_to_recording_provider(provider_lane):
    agent, messages, provider, queued = provider_lane
    sync(agent, messages)
    assert len(queued) == 2
    for call in queued:
        call()
    assert [row[0] for row in provider.calls] == ['sync', 'prefetch']
    assert provider.calls[0][1:3] == ('Synthetic reference about blue widgets', 'Synthetic reference response')
    assert provider.calls[0][3]['messages'] == messages
    assert provider.calls[0][3]['turn_author'] == agent._turn_author


def test_seal_does_not_cancel_previously_queued_ordinary_work(provider_lane):
    agent, messages, provider, queued = provider_lane
    sync(agent, messages, original_user_message='Earlier ordinary synthetic question')
    assert len(queued) == 2
    policy = seal(agent)
    sync(agent, messages)
    assert len(queued) == 2
    for call in queued:
        call()
    assert [row[1] for row in provider.calls] == [
        'Earlier ordinary synthetic question', 'Earlier ordinary synthetic question']
    assert retained_policy(agent) is policy
