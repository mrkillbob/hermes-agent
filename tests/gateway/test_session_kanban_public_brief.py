"""Native owned remote workers start from the admitted title/body, not control-plane history."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gateway.session_kanban import _run_task_turns


@pytest.fixture
def startup(monkeypatch):
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_public')
    monkeypatch.delenv('HERMES_DELEGATED_CHILD_CONTEXT', raising=False)
    loaded = Mock(return_value=('Explicit assigned skill instructions.', ['assigned-skill'], []))
    monkeypatch.setattr('agent.skill_commands.build_preloaded_skills_prompt', loaded)
    monkeypatch.setattr('agent.kanban_turn_recovery.recover_failed_kanban_turns', lambda *a, **kw: None)
    agent = SimpleNamespace(session_id='session-public', provider='custom:unlisted',
        base_url='https://remote.example.test/v1', api_mode='chat_completions',
        run_conversation=Mock(return_value={'completed': True, 'final_response': 'done'}))
    context = {'task_id': 't_public', 'goal_text': 'Public title\n\nApproved public assignment.',
        'context': 'Private prior attempt: evening ferry.\nAttachment /Users/private/receipt.json\n'
                   'Qualification metadata: 0123456789abcdef0123456789abcdef',
        'skills': ['assigned-skill'], 'goal_mode': False}
    return agent, context, loaded


@pytest.mark.parametrize('scope', ['owned_remote', 'owned_local', 'operator', 'delegated'])
def test_actual_native_startup_selects_public_brief_only_for_owned_remote(startup, monkeypatch, scope):
    agent, context, loaded = startup
    if scope == 'owned_local':
        agent.base_url = 'http://127.0.0.1:8000/v1'
    if scope == 'operator':
        monkeypatch.delenv('HERMES_KANBAN_TASK')
    if scope == 'delegated':
        monkeypatch.setenv('HERMES_DELEGATED_CHILD_CONTEXT', '1')
    original = deepcopy(context)
    frame = {'text': 'work kanban task t_public'}
    history = []
    _run_task_turns(agent, frame, history, context)
    prompt = agent.run_conversation.call_args.args[0]
    assert 'Explicit assigned skill instructions.' in prompt
    assert prompt.endswith(frame['text'])
    assert ('evening ferry' in prompt) is (scope != 'owned_remote')
    assert ('/Users/private' in prompt) is (scope != 'owned_remote')
    assert ('0123456789abcdef' in prompt) is (scope != 'owned_remote')
    if scope == 'owned_remote':
        assert context['goal_text'] in prompt
        assert 'relative' in prompt and 'kanban_complete' in prompt and 'kanban_block' in prompt
    else:
        assert context['context'] in prompt
    loaded.assert_called_once_with(['assigned-skill'], task_id=agent.session_id)
    assert agent.run_conversation.call_args.kwargs['conversation_history'] is history
    assert context == original


@pytest.mark.parametrize('brief', [None, '', '  ', 123, ['not admitted text']])
def test_owned_remote_missing_or_invalid_public_brief_never_falls_back(startup, brief):
    agent, context, loaded = startup
    context['goal_text'] = brief
    with pytest.raises(ValueError, match='kanban_public_brief_unavailable'):
        _run_task_turns(agent, {'text': 'work'}, [], context)
    agent.run_conversation.assert_not_called()
    loaded.assert_not_called()


def test_owned_remote_absent_public_brief_never_falls_back(startup):
    agent, context, loaded = startup
    del context['goal_text']
    with pytest.raises(ValueError, match='kanban_public_brief_unavailable'):
        _run_task_turns(agent, {'text': 'work'}, [], context)
    agent.run_conversation.assert_not_called()
    loaded.assert_not_called()
