"""Retained diagnostic seal before real dispatch, with inert boundary sentinels."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context, copy_context

import pytest

from agent.diagnostic_reference_policy import DiagnosticPolicy, bind_agent
from agent.local_text_policy import LocalTextRestriction, local_text_scope


class DispatchReached(BaseException):
    """Inert test boundary; no hook, handler, memory write or model runs."""


class Batch:
    @property
    def tool_calls(self):
        raise DispatchReached('batch input reached')


_ENTRIES = ['single', 'execute_tool_calls_concurrent', 'execute_tool_calls_sequential',
            '_execute_tool_calls_sequential', 'execute_tool_calls_segmented']


@pytest.fixture
def agent():
    from run_agent import AIAgent
    # Real agent class, synthetic uninitialized state. This is not a session,
    # transport authentication, note permission, or model execution fixture.
    instance = AIAgent.__new__(AIAgent)
    instance.session_id = 'synthetic-session'
    return instance


def dispatch(agent, entry):
    if entry == 'single':
        from agent.agent_runtime_helpers import invoke_tool
        return invoke_tool(agent, 'memory', {'content': 'synthetic-reference'}, 'synthetic-task')
    from agent import tool_executor
    return getattr(tool_executor, entry)(agent, Batch(), [], 'synthetic-task')


@pytest.fixture(autouse=True)
def inert_middleware(monkeypatch):
    def reached(*args, **kwargs):
        raise DispatchReached('middleware input reached')
    monkeypatch.setattr('hermes_cli.middleware.apply_tool_request_middleware', reached)


def seal(agent):
    bind_agent(agent, DiagnosticPolicy('synthetic-host-request', '{}', agent.session_id))


@pytest.mark.parametrize('entry', _ENTRIES)
def test_sealed_agent_refuses_before_dispatch_or_observer_input(agent, entry):
    seal(agent)
    with pytest.raises(LocalTextRestriction, match='diagnostic_reference_tools_denied'):
        dispatch(agent, entry)


@pytest.mark.parametrize('entry', _ENTRIES)
def test_ordinary_agent_preserves_existing_dispatch_path(agent, entry):
    with pytest.raises(DispatchReached):
        dispatch(agent, entry)


def test_copied_context_does_not_clear_retained_tool_refusal(agent):
    seal(agent)
    with pytest.raises(LocalTextRestriction, match='diagnostic_reference_tools_denied'):
        copy_context().run(dispatch, agent, 'single')


def test_empty_context_in_raw_worker_does_not_clear_retained_tool_refusal(agent):
    seal(agent)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(Context().run, dispatch, agent, 'single')
        with pytest.raises(LocalTextRestriction, match='diagnostic_reference_tools_denied'):
            pending.result(timeout=5)


def test_session_id_change_cannot_turn_retained_seal_into_permission(agent):
    seal(agent)
    agent.session_id = 'replacement-session'
    with pytest.raises(LocalTextRestriction, match='diagnostic_reference_tools_denied'):
        dispatch(agent, 'single')


@pytest.mark.parametrize('entry', _ENTRIES)
def test_unsealed_agent_still_obeys_existing_local_text_restriction(agent, entry):
    with local_text_scope(True), pytest.raises(LocalTextRestriction, match='local_text_tools_denied'):
        dispatch(agent, entry)


@pytest.mark.parametrize('flags', [
    {'skip_tool_request_middleware': True},
    {'skip_tool_execution_middleware': True},
    {'pre_tool_block_checked': True, 'skip_tool_request_middleware': True,
     'skip_tool_execution_middleware': True},
])
def test_skip_flags_cannot_disable_retained_refusal(agent, monkeypatch, flags):
    from agent.agent_runtime_helpers import invoke_tool
    seal(agent)

    def reached(*args, **kwargs):
        raise DispatchReached('tool preparation reached')

    monkeypatch.setattr('agent.agent_runtime_helpers._pre_tool_block_message', reached)
    monkeypatch.setattr('agent.inline_tool_executors.resolve_invoke_tool_executor', reached)
    with pytest.raises(LocalTextRestriction, match='diagnostic_reference_tools_denied'):
        invoke_tool(agent, 'memory', {'content': 'synthetic-reference'}, 'synthetic-task', **flags)
