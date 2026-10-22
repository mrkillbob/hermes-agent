"""Capacity presentation follows the actual tool owner's protected route, not metadata."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.source_provenance import active_source_provenance
from agent.source_provenance_tools import source_provenance_activation
from agent.subdirectory_hints import SubdirectoryHintTracker
from tests.agent.test_llm_egress_runtime import _agent


def hint(tmp_path, monkeypatch, shape, provider):
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    root = tmp_path/'repo'
    area = root/'gateway'
    area.mkdir(parents=True)
    source = area/'module.py'
    source.write_text('value = 1\n')
    text = {'bytes':('source guidance. '*30+'\n')*600,
            'lines':'source guidance.\n'*2001,
            'single_line':'source guidance. '*16000}[shape]
    (area/'AGENTS.md').write_text(text)
    agent = _agent(tmp_path/'egress')
    agent._current_api_request_id = 'turn-1:api:1'
    agent.provider = provider
    agent.base_url = 'http://127.0.0.1:11434/v1' if provider == 'ollama' else 'https://api.nousresearch.com/v1'
    tracker = SubdirectoryHintTracker(str(root))
    with source_provenance_activation(agent, 'read_file'):
        return tracker.check_tool_call('read_file', {'path':str(source)})


@pytest.mark.parametrize('shape', ['bytes', 'lines', 'single_line'])
@pytest.mark.parametrize('provider', ['ollama', 'Nous'])
def test_only_protected_owner_loses_unsupported_preview(tmp_path, monkeypatch, shape, provider):
    result = hint(tmp_path, monkeypatch, shape, provider)
    if provider == 'Nous':
        assert '[UNAVAILABLE:' in result
        assert 'source guidance.' not in result
    else:
        assert '[UNAVAILABLE:' not in result
        assert 'source guidance.' in result
        assert 'truncated AGENTS.md' in result


def owner(tmp_path, provider, label):
    agent = _agent(tmp_path/label)
    agent.session_id = label
    agent._current_api_request_id = 'turn-1:api:1'
    agent.provider = provider
    agent.base_url = 'http://127.0.0.1:11434/v1' if provider == 'ollama' else 'https://example.org/v1'
    return agent


def test_nested_owner_scope_restores_after_exception_without_leaking(tmp_path, monkeypatch):
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    remote, local = owner(tmp_path, 'Nous', 'remote'), owner(tmp_path, 'ollama', 'local')
    assert active_source_provenance() is None
    with source_provenance_activation(remote, 'read_file'):
        outer = active_source_provenance()
        assert outer.capacity_notice_on_source_limit is True
        with pytest.raises(RuntimeError):
            with source_provenance_activation(local, 'read_file'):
                inner = active_source_provenance()
                assert inner.session_id == 'local'
                assert inner.capacity_notice_on_source_limit is False
                assert inner.registry is local._source_provenance_registry
                raise RuntimeError('fixture')
        assert active_source_provenance() is outer
    assert active_source_provenance() is None


def test_concurrent_owner_scope_never_borrows_protection_or_identity(tmp_path, monkeypatch):
    from threading import Barrier
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    barrier = Barrier(2)

    def observe(agent):
        with source_provenance_activation(agent, 'read_file'):
            barrier.wait(timeout=5)
            context = active_source_provenance()
            result = (context.session_id, context.turn_id, context.request_id,
                      context.policy_digest, context.capacity_notice_on_source_limit)
        assert active_source_provenance() is None
        return result

    agents = [owner(tmp_path, 'Nous', 'remote'), owner(tmp_path, 'ollama', 'local')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        remote, local = list(pool.map(observe, agents))
    assert remote[0] == 'remote' and remote[-1] is True
    assert local[0] == 'local' and local[-1] is False
    assert remote[1:4] == local[1:4]
    assert active_source_provenance() is None


def test_inherited_task_does_not_make_unowned_custom_route_protected(tmp_path, monkeypatch):
    from agent.delegation_context import non_dispatcher_owned_context
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    monkeypatch.setenv('HERMES_KANBAN_TASK', 'generated-task')
    agent = owner(tmp_path, 'custom', 'owner')
    with source_provenance_activation(agent, 'read_file'):
        assert active_source_provenance().capacity_notice_on_source_limit is True
    with non_dispatcher_owned_context(), source_provenance_activation(agent, 'read_file'):
        assert active_source_provenance().capacity_notice_on_source_limit is False


def test_existing_marker_and_actual_route_fallback_are_recomputed(tmp_path, monkeypatch):
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    agent = owner(tmp_path, 'ollama', 'owner')
    for provider, expected in [('ollama', False), ('Nous', True), ('ollama', False)]:
        agent.provider = provider
        with source_provenance_activation(agent, 'read_file'):
            assert active_source_provenance().capacity_notice_on_source_limit is expected
    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    with source_provenance_activation(agent, 'read_file'):
        assert active_source_provenance().capacity_notice_on_source_limit is True


def test_unavailable_route_does_not_invent_eligibility_or_remove_identity(tmp_path, monkeypatch):
    import agent.llm_egress_runtime as runtime
    def unavailable(*_args):
        raise ValueError('route unavailable')
    monkeypatch.setattr(runtime, '_route_for_agent', unavailable)
    agent = owner(tmp_path, 'Nous', 'owner')
    with source_provenance_activation(agent, 'read_file'):
        context = active_source_provenance()
        assert context.session_id == 'owner'
        assert context.capacity_notice_on_source_limit is False


def test_sidecar_like_metadata_cannot_enable_local_capacity_replacement(tmp_path, monkeypatch):
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    agent = owner(tmp_path, 'ollama', 'owner')
    agent._source_provenance_metadata = {'capacity_notice_on_source_limit':True, 'provider':'Nous'}
    with source_provenance_activation(agent, 'read_file'):
        assert active_source_provenance().capacity_notice_on_source_limit is False
