"""Behavior contracts for a preloader reaching its startup deadline.

The thread seam represents join returning at its deadline, without a 120s wait.
"""
from types import SimpleNamespace

import pytest

import cli


class _DeadlineThread:
    """A preloader still running when its bounded join returns."""

    def __init__(self):
        self.alive = True

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return self.alive


class _ReadyForCallbacks(Exception):
    pass


def _shell():
    shell = cli.HermesCLI.__new__(cli.HermesCLI)
    shell.agent = None
    shell.system_prompt = 'base'
    shell.preloaded_skills = []
    shell._preload_skills_thread = _DeadlineThread()
    shell._auto_load_skills_result = ('', [], [])
    return shell


@pytest.mark.parametrize('kind', ['explicit', 'auto_load'])
def test_preload_deadline_refuses_startup_then_accepts_late_result(monkeypatch, kind):
    shell = _shell()
    messages = []
    monkeypatch.setattr(cli, 'ChatConsole', lambda: SimpleNamespace(print=messages.append))
    calls = []

    def callbacks():
        calls.append('callbacks')
        raise _ReadyForCallbacks

    def forbidden():
        pytest.fail('Deadline must return before credential, gateway or provider work')

    shell._install_tool_callbacks = callbacks
    shell._ensure_runtime_credentials = forbidden
    shell._ensure_gateway_session = forbidden

    assert shell._init_agent() is False
    assert calls == []
    assert len(messages) == 1
    assert 'Skill loading is still in progress. Please try again.' in messages[0]
    assert shell.system_prompt == 'base'
    assert not getattr(shell, '_preload_skills_finalized', False)

    shell._preload_skills_thread.alive = False
    if kind == 'explicit':
        shell._preload_skills_result = ('skill instructions', ['example'], [])
    else:
        # Auto-load text is assembled by the existing auto-load prompt path;
        # finalization records its loaded names while startup waits for it.
        shell._auto_load_skills_result = ('', ['example'], [])
    with pytest.raises(_ReadyForCallbacks):
        shell._init_agent()
    assert calls == ['callbacks']
    assert shell._preload_skills_finalized is True
    expected_prompt = 'base\n\nskill instructions' if kind == 'explicit' else 'base'
    assert shell.system_prompt == expected_prompt
    assert shell.preloaded_skills == ['example']
    shell.finalize_preloaded_skills()
    assert shell.system_prompt == expected_prompt
    assert shell.preloaded_skills == ['example']


@pytest.mark.parametrize('failure_kind', ['value_error', 'timeout_error', 'missing_explicit'])
def test_preloader_failure_remains_a_refusal_on_repeat(monkeypatch, failure_kind):
    shell = _shell()
    shell._preload_skills_thread.alive = False
    failure_type = TimeoutError if failure_kind == 'timeout_error' else ValueError
    if failure_kind == 'missing_explicit':
        shell._preload_skills_result = ('', [], ['unavailable-example'])
    else:
        shell._preload_skills_error = failure_type('synthetic skill failure')
    messages = []
    monkeypatch.setattr(cli, 'ChatConsole', lambda: SimpleNamespace(print=messages.append))

    def forbidden():
        pytest.fail('Preload refusal must not reach callbacks, credentials or gateway work')

    shell._install_tool_callbacks = forbidden
    shell._ensure_runtime_credentials = forbidden
    shell._ensure_gateway_session = forbidden
    for _ in range(2):
        with pytest.raises(failure_type):
            shell._init_agent()
        assert not getattr(shell, '_preload_skills_finalized', False)
        assert shell.system_prompt == 'base'
    assert messages == []
