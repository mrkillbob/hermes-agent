"""Unverified history never becomes public merely by rebuilding SYSTEM."""
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from agent.llm_egress_firewall import EgressBlocked
from tests.agent.test_remote_task_egress import _agent
@pytest.fixture
def owned_task(monkeypatch):
    import agent.llm_egress_runtime as runtime
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_public')
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    monkeypatch.delenv('HERMES_DELEGATED_CHILD_CONTEXT', raising=False)
    monkeypatch.setattr(runtime, 'egress_enforcement_enabled', lambda: True)
    monkeypatch.setattr(runtime, '_exact_provider_secret_values', lambda: ())


def _public(agent):
    agent._cached_system_prompt = 'Public task instructions.'
    agent._remote_task_context_mode = 'public'
    agent._remote_task_public_prompt = agent._cached_system_prompt


def _reject_remote(agent, messages):
    from agent.chat_completion_helpers import _dispatch_provider_request
    agent.base_url = 'https://remote.example.test/v1'
    _public(agent)  # A compression/prompt rebuild cannot attest retained history.
    before = deepcopy(messages)
    with pytest.raises(EgressBlocked) as blocked:
        _dispatch_provider_request(agent, {'model': 'test-model', 'messages': messages},
            lambda body: pytest.fail('Unverified history reached remote provider'))
    assert blocked.value.decision.reason_codes == ('unverified_remote_task_history',)
    assert messages == before


def _dispatch_local(agent, messages, *, direct=False):
    from agent.chat_completion_helpers import _dispatch_provider_request
    from agent.llm_egress_runtime import dispatch_authorized_agent_request
    dispatch = dispatch_authorized_agent_request if direct else _dispatch_provider_request
    assert dispatch(agent, {'messages': messages}, lambda body: 'local response') == 'local response'


def _compose_local(agent, _messages):
    from agent.remote_task_context import compose_task_system_prompt
    agent.ephemeral_system_prompt = 'Private household preference: evening ferry.'
    assert 'evening ferry' in compose_task_system_prompt(agent, agent._cached_system_prompt)


def _recall_local(agent, _messages):
    from agent.turn_context import _memory_turn_start_and_prefetch
    agent._memory_manager = None
    _memory_turn_start_and_prefetch(agent, 'Public request.')


def _hook_local(agent, messages):
    from agent.turn_context import _collect_pre_llm_call_context
    agent._persist_disabled = True
    _collect_pre_llm_call_context(agent, effective_task_id='t_public', turn_id='turn',
        original_user_message='Public request.', messages=messages, conversation_history=[])


@pytest.mark.parametrize('producer', [_dispatch_local, _compose_local, _recall_local, _hook_local])
def test_owned_local_context_cannot_return_to_remote(tmp_path, owned_task, producer):
    agent = _agent(tmp_path, 'http://127.0.0.1:8000/v1')
    _public(agent)
    messages = [{'role': 'user', 'content': 'Public request.',
                 'api_content': 'Public request. Private household preference: evening ferry.'}]
    producer(agent, messages)
    _reject_remote(agent, messages)


def test_direct_local_dispatch_also_marks_history(tmp_path, owned_task):
    agent = _agent(tmp_path, 'http://127.0.0.1:8000/v1')
    _public(agent)
    messages = [{'role': 'user', 'content': 'Public request.'}]
    _dispatch_local(agent, messages, direct=True)
    _reject_remote(agent, messages)


@pytest.mark.parametrize('remote_main', [True, False])
def test_compression_attempt_cannot_attest_flattened_history(tmp_path, monkeypatch, owned_task, remote_main):
    from agent import conversation_compression as compression
    agent = _agent(tmp_path, 'https://remote.example.test/v1' if remote_main else 'http://127.0.0.1:8000/v1')
    _public(agent)
    messages = [{'role': 'user', 'content': 'Private summary: evening ferry.'}]
    class StopBeforeProviders(Exception):
        pass
    monkeypatch.setattr(compression, '_begin_compression_attempt', MagicMock(side_effect=StopBeforeProviders))
    with pytest.raises(StopBeforeProviders):
        compression.compress_context(agent, messages, agent._cached_system_prompt)
    _reject_remote(agent, messages)


@pytest.mark.parametrize('stored', ['missing', None, '', 'mismatch', 'db_error'])
def test_rebuilt_public_prompt_does_not_attest_restored_history(tmp_path, monkeypatch, owned_task, stored):
    from agent import conversation_loop as loop
    from tests.agent.test_system_prompt_restore import _make_agent
    db = MagicMock()
    db.get_session.return_value = None if stored == 'missing' else {'system_prompt': stored}
    if stored == 'db_error':
        db.get_session.side_effect = RuntimeError('offline fixture')
    agent = _make_agent(session_db=db)
    agent.base_url = 'https://remote.example.test/v1'
    agent.api_mode = 'chat_completions'
    agent._llm_egress_state_dir = tmp_path / 'receipts'
    agent._llm_egress_policy_digest = _agent(tmp_path)._llm_egress_policy_digest
    agent._remote_task_history_unverified = False
    monkeypatch.setattr(loop, '_stored_prompt_matches_runtime', lambda *_: False)
    history = [{'role': 'user', 'content': 'Private household preference: evening ferry.'}]
    loop._restore_or_build_system_prompt(agent, None, history)
    _reject_remote(agent, history)


def test_adopted_child_history_is_unverified_even_with_same_public_prompt(tmp_path, owned_task):
    from agent.conversation_compression import _adopt_live_compression_child
    from hermes_state import SessionDB
    agent = _agent(tmp_path)
    _public(agent)
    agent.pass_session_id = False
    agent.context_compressor = agent._memory_manager = None
    db = SessionDB(tmp_path / 'state.db')
    try:
        db.create_session('parent', source='kanban')
        db.end_session('parent', 'compression')
        db.create_session('child', source='kanban', parent_session_id='parent')
        db.update_system_prompt('child', agent._cached_system_prompt)
        db.append_message('child', 'user', 'Private summary: evening ferry.')
        agent.session_id = 'parent'
        history = _adopt_live_compression_child(agent, db, 'parent')
        assert history
        assert agent._cached_system_prompt == agent._remote_task_public_prompt
        _reject_remote(agent, history)
    finally:
        db.close()


def test_operator_local_history_and_fresh_public_session_remain_usable(tmp_path, monkeypatch, owned_task):
    from agent.chat_completion_helpers import _dispatch_provider_request
    from agent.remote_task_context import compose_task_system_prompt
    monkeypatch.delenv('HERMES_KANBAN_TASK')
    operator = _agent(tmp_path, 'http://127.0.0.1:8000/v1')
    operator.ephemeral_system_prompt = 'Private operator context.'
    assert 'Private operator' in compose_task_system_prompt(operator, 'SYSTEM')
    assert not getattr(operator, '_remote_task_history_unverified', False)
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_public')
    fresh = _agent(tmp_path)
    _public(fresh)
    request = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'Public request.'}]}
    assert _dispatch_provider_request(fresh, request, lambda body: dict(body)) == request
