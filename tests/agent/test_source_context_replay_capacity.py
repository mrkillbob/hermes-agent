"""Composite replays consume one current-request proof per actual source."""
import pytest

from agent.llm_egress_firewall import EgressBlocked, source_grant_digest
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance import MAX_GRANTS_PER_REQUEST
from agent.source_provenance_tools import attach_trusted_source_provenance_metadata, source_provenance_activation
from agent.subdirectory_hints import SubdirectoryHintTracker
from agent.tool_dispatch_helpers import make_tool_result_message
from tests.agent.test_llm_egress_runtime import _agent
from tools.file_tools import read_file_tool


def validated_composites(tmp_path, monkeypatch, count, *, batched=False, shared_guidance=True, shared_prefix=False, bind_call_ids=False):
    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    agent = _agent(tmp_path / 'egress')
    messages = []
    for index in range(count):
        root = tmp_path / str(index) / 'repo'
        area = root / 'gateway'
        area.mkdir(parents=True)
        source = area / 'module.py'
        source.write_text(f'value = {0 if shared_prefix else index}\n')
        (area / 'AGENTS.md').write_text('Preserve repository guidance.\n' if shared_guidance else f'Preserve repository guidance {index}.\n')
        agent._current_api_request_id = 'turn-1:api:1' if batched else f'turn-1:api:{index * 2 + 1}'
        tracker = SubdirectoryHintTracker(str(root))
        from agent.source_provenance_tools import execute_with_source_provenance

        call_id = f'call_context_{index}'
        result = execute_with_source_provenance(
            agent, 'read_file', call_id if bind_call_ids else '',
            lambda arguments: read_file_tool(arguments['path'], task_id='replay-capacity'), {'path':str(source)})
        with source_provenance_activation(agent, 'read_file'):
            hint = tracker.check_tool_call('read_file', {'path': str(source)})
        assert hint
        content = result + hint
        metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=content,
                                                               tool_call_id=call_id if bind_call_ids else None)
        message = make_tool_result_message('read_file', content, f'call_context_{index}', source_provenance=metadata)
        if not batched:
            agent._current_api_request_id = f'turn-1:api:{index * 2 + 2}'
            _, receipt = authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':[message]})
            assert receipt.decision.source_grant_count == 2
        messages.append(message)
    if batched:
        agent._current_api_request_id = 'turn-1:api:2'
        _, receipt = authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages})
        assert receipt.decision.source_grant_count == count * 2
    return agent, messages


@pytest.mark.parametrize('count', [1, 17, MAX_GRANTS_PER_REQUEST // 2])
def test_repeated_composite_replays_use_exactly_two_proofs_per_result(tmp_path, monkeypatch, count):
    agent, messages = validated_composites(tmp_path, monkeypatch, count)
    registry = agent._source_provenance_registry
    for request_number in (1000, 1001, 1002):
        request_id = f'turn-1:api:{request_number}'
        agent._current_api_request_id = request_id
        authorized, receipt = authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages})
        assert [message['content'] for message in authorized['messages']] == [message['content'] for message in messages]
        assert all('_source_provenance' not in message for message in authorized['messages'])
        assert receipt.decision.source_grant_count == count * 2
        assert receipt.decision.source_segment_count == count * 2
        grants = registry.grants_for_request(request_id)
        assert len(grants) == count * 2
        assert len({source_grant_digest(grant) for grant in grants}) == count * 2


def test_composite_replay_beyond_real_registry_cap_is_refused(tmp_path, monkeypatch):
    agent, messages = validated_composites(tmp_path, monkeypatch, MAX_GRANTS_PER_REQUEST // 2 + 1)
    agent._current_api_request_id = 'turn-1:api:1000'
    with pytest.raises(EgressBlocked) as blocked:
        authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages})
    assert 'untrusted_provenance' in blocked.value.decision.reason_codes
    assert len(agent._source_provenance_registry.grants_for_request('turn-1:api:1000')) == MAX_GRANTS_PER_REQUEST


@pytest.mark.parametrize('changed', ['module.py', 'AGENTS.md'])
def test_replay_proof_reuse_refuses_changed_prefix_and_guidance(tmp_path, monkeypatch, changed):
    agent, messages = validated_composites(tmp_path, monkeypatch, 1)
    (tmp_path / '0/repo/gateway' / changed).write_text('Changed after prior validation.\n')
    agent._current_api_request_id = 'turn-1:api:1000'
    with pytest.raises(EgressBlocked) as blocked:
        authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages})
    assert 'untrusted_provenance' in blocked.value.decision.reason_codes


@pytest.mark.parametrize('count', [17, MAX_GRANTS_PER_REQUEST // 2])
@pytest.mark.parametrize('guidance', ['same', 'distinct'])
def test_same_request_cumulative_metadata_replays_each_source_once(tmp_path, monkeypatch, count, guidance):
    agent, messages = validated_composites(tmp_path, monkeypatch, count, batched=True, shared_guidance=guidance == 'same')
    assert len(messages[0]['_source_provenance']['source_grant_digests']) == 2
    assert len(messages[-1]['_source_provenance']['source_grant_digests']) == count * 2
    for number in (3, 3, 4, 5):
        request_id = f'turn-1:api:{number}'
        agent._current_api_request_id = request_id
        authorized, receipt = authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages})
        assert [message['content'] for message in authorized['messages']] == [message['content'] for message in messages]
        assert all('_source_provenance' not in message for message in authorized['messages'])
        assert receipt.decision.source_grant_count == count * 2
        assert receipt.decision.source_segment_count == count * 2
        grants = agent._source_provenance_registry.grants_for_request(request_id)
        assert len(grants) == len({source_grant_digest(grant) for grant in grants}) == count * 2


@pytest.mark.parametrize('case', ['unremembered', 'malformed'])
def test_old_unvalidated_metadata_cannot_borrow_identical_current_grants(tmp_path, monkeypatch, case):
    agent, messages = validated_composites(tmp_path, monkeypatch, 1)
    registry = agent._source_provenance_registry
    registry.clear_turn('turn-1')
    if case == 'malformed':
        messages[0]['_source_provenance']['source_grant_digests'] = ['invalid-old-proof']
    area = tmp_path / '0/repo/gateway'
    agent._current_api_request_id = 'turn-1:api:2'
    with source_provenance_activation(agent, 'read_file'):
        read_file_tool(str(area / 'module.py'), task_id='fresh-current-proof')
        SubdirectoryHintTracker(str(area.parent)).check_tool_call('read_file', {'path':str(area / 'module.py')})
    assert len(registry.grants_for_request('turn-1:api:3')) == 2
    agent._current_api_request_id = 'turn-1:api:3'
    with pytest.raises(EgressBlocked) as blocked:
        authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages})
    assert 'untrusted_provenance' in blocked.value.decision.reason_codes


def test_reusing_current_request_proof_rechecks_read_policy(tmp_path, monkeypatch):
    import agent.source_provenance as provenance

    agent, messages = validated_composites(tmp_path, monkeypatch, 1)
    agent._current_api_request_id = 'turn-1:api:3'
    authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages})
    original = provenance.get_read_block_error
    monkeypatch.setattr(provenance, 'get_read_block_error',
                        lambda path: 'denied' if str(path).endswith('AGENTS.md') else original(path))
    with pytest.raises(EgressBlocked) as blocked:
        authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages})
    assert 'untrusted_provenance' in blocked.value.decision.reason_codes


def test_unique_producer_grants_keep_actual_registry_cap(tmp_path):
    from agent.source_provenance import SourceProvenanceError, SourceProvenanceRegistry

    registry = SourceProvenanceRegistry()
    identity = dict(session_id='session-1', turn_id='turn-1', request_id='turn-1:api:1', policy_digest='policy-1')
    for index in range(MAX_GRANTS_PER_REQUEST + 1):
        path = tmp_path / f'source{index}.py'
        path.write_text('value = 1\n')
        if index == MAX_GRANTS_PER_REQUEST:
            with pytest.raises(SourceProvenanceError, match='grant_limit_exceeded'):
                registry.issue_file_slice(path=path, line_start=1, line_end=1, content=path.read_bytes(), **identity)
        else:
            registry.issue_file_slice(path=path, line_start=1, line_end=1, content=path.read_bytes(), **identity)
    assert len(registry.grants_for_request('turn-1:api:1')) == MAX_GRANTS_PER_REQUEST


@pytest.mark.parametrize('case', ['changed', 'denied', 'missing_grant'])
def test_same_text_at_other_path_cannot_replace_one_guidance_proof(tmp_path, monkeypatch, case):
    import agent.source_provenance as provenance

    agent, messages = validated_composites(tmp_path, monkeypatch, 3, batched=True)
    path = tmp_path / '1/repo/gateway/AGENTS.md'
    if case == 'changed':
        path.write_text('Changed after source validation.\n')
    elif case == 'denied':
        original = provenance.get_read_block_error
        monkeypatch.setattr(provenance, 'get_read_block_error',
                            lambda candidate: 'denied' if str(candidate) == str(path) else original(candidate))
    else:
        grants = agent._source_provenance_registry.grants_for_request('turn-1:api:2')
        missing = next(source_grant_digest(grant) for grant in grants if grant.canonical_path == path.resolve())
        metadata = messages[1]['_source_provenance']
        metadata['source_grant_digests'] = tuple(digest for digest in metadata['source_grant_digests'] if digest != missing)
    agent._current_api_request_id = 'turn-1:api:3'
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages})
