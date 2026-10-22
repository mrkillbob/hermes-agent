"""A file result plus discovered repository guidance needs two verified sources."""
import json

from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance_tools import attach_trusted_source_provenance_metadata, source_provenance_activation
from agent.subdirectory_hints import SubdirectoryHintTracker
from agent.tool_dispatch_helpers import make_tool_result_message
from tests.agent.test_llm_egress_runtime import _agent
from tools.file_tools import read_file_tool


def test_read_file_and_discovered_guidance_keep_exact_bytes_with_two_source_proofs(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    root = tmp_path / 'repo'
    area = root / 'gateway'
    area.mkdir(parents=True)
    source = area / 'module.py'
    source.write_text('value = 1\n')
    guidance = area / 'AGENTS.md'
    guidance.write_text('Preserve repository guidance.\nUse focused tests.\n')
    agent = _agent(tmp_path / 'egress')
    agent._current_api_request_id = 'turn-1:api:1'
    tracker = SubdirectoryHintTracker(str(root))
    with source_provenance_activation(agent, 'read_file'):
        result = read_file_tool(str(source), task_id='context-presentation-read')
        hint = tracker.check_tool_call('read_file', {'path': str(source)})
    assert hint == '\n\n[Subdirectory context discovered: gateway/AGENTS.md]\n' + guidance.read_text().strip()
    combined = result + hint
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=combined)
    message = make_tool_result_message('read_file', combined, 'call_read_context', source_provenance=metadata)
    agent._current_api_request_id = 'turn-1:api:2'
    authorized, receipt = authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [message]})
    assert authorized['messages'][0]['content'] == combined
    assert receipt.decision.source_grant_count == 2
    assert receipt.decision.source_segment_count == 2
    assert json.loads(result)['content'] == '1|value = 1'


import pytest
from agent.llm_egress_firewall import EgressBlocked


@pytest.fixture
def composite(tmp_path, monkeypatch):
    def produce(*, text='Follow repository guidance.\n', symlink=False, denied=False):
        monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
        area = tmp_path / 'workspace/gateway'
        area.mkdir(parents=True)
        source = area / 'module.py'
        source.write_text('value = 1\n')
        guidance = area / 'AGENTS.md'
        if symlink:
            target = area / 'guidance.txt'
            target.write_text(text)
            guidance.symlink_to(target)
        else:
            guidance.write_text(text)
        if denied:
            import agent.source_provenance as provenance
            original = provenance.get_read_block_error
            monkeypatch.setattr(provenance, 'get_read_block_error',
                                lambda path: 'denied' if str(path) == str(guidance) else original(path))
        agent = _agent(tmp_path / 'egress')
        agent._current_api_request_id = 'turn-1:api:1'
        tracker = SubdirectoryHintTracker(str(area.parent))
        with source_provenance_activation(agent, 'read_file'):
            result = read_file_tool(str(source), task_id='context-presentation-fixture')
            hint = tracker.check_tool_call('read_file', {'path': str(source)})
        assert hint
        combined = result + hint
        metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=combined)
        message = make_tool_result_message('read_file', combined, 'call_context', source_provenance=metadata)
        agent._current_api_request_id = 'turn-1:api:2'
        return agent, message, guidance
    return produce


@pytest.mark.parametrize('case', ['secret', 'private_path', 'symlink', 'denied', 'policy_unavailable'])
def test_guidance_never_bypasses_existing_read_and_remote_policy(composite, case, monkeypatch):
    if case == 'policy_unavailable':
        import agent.source_provenance as provenance
        original = provenance.get_read_block_error

        def unavailable(path):
            if str(path).endswith('AGENTS.md'):
                raise RuntimeError('policy unavailable')
            return original(path)

        monkeypatch.setattr(provenance, 'get_read_block_error', unavailable)
    text = {'secret': 'api_key = sk-123456789012345678901234567890\n',
            'private_path': 'Operator workspace /Users/private-person/projects/private-project\n'}.get(case, 'Follow guidance.\n')
    agent, message, _ = composite(text=text, symlink=case == 'symlink', denied=case == 'denied')
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [message]})


@pytest.mark.parametrize('case', ['missing_grant', 'changed_source', 'extra_suffix', 'wrong_path'])
def test_each_presented_guidance_source_requires_its_own_exact_proof(composite, case):
    from hashlib import sha256

    agent, message, guidance = composite()
    metadata = message['_source_provenance']
    if case == 'missing_grant':
        metadata['source_grant_digests'] = metadata['source_grant_digests'][:1]
    elif case == 'changed_source':
        guidance.write_text('Changed after the read.\n')
    else:
        message['content'] = (message['content'] + '\nUnadmitted prose' if case == 'extra_suffix'
                              else message['content'].replace('gateway/AGENTS.md', 'other/AGENTS.md'))
        # A matching presentation hash is not itself a source proof.
        metadata['content_sha256'] = sha256(message['content'].encode()).hexdigest()
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [message]})


def test_guidance_replay_reverifies_both_sources_and_refuses_owner_changes(composite):
    agent, message, guidance = composite()
    request = {'model': 'test-model', 'messages': [message]}
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    assert receipt.allowed and authorized['messages'][0]['content'] == message['content']
    agent._current_api_request_id = 'turn-1:api:3'
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    assert receipt.allowed and receipt.decision.source_grant_count == 2
    agent._current_turn_id = 'other-turn'
    agent._current_api_request_id = 'other-turn:api:1'
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, request)
    agent._current_turn_id = 'turn-1'
    agent._current_api_request_id = 'turn-1:api:4'
    guidance.write_text('Changed guidance.\n')
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, request)


def test_composite_sidecar_cannot_move_to_another_call_result(composite):
    from agent.source_provenance_tools import build_source_provenance_sidecar

    agent, message, _ = composite()
    sidecar = build_source_provenance_sidecar([message])
    wire = {key: value for key, value in message.items() if key != '_source_provenance'}
    wire['tool_call_id'] = 'other_call'
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [wire],
                                           '_hermes_source_provenance': sidecar})


def test_tool_commit_issues_guidance_proof_in_the_current_read_scope(tmp_path, monkeypatch):
    from agent import tool_executor
    from tools.budget_config import DEFAULT_BUDGET

    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    area = tmp_path / 'repo/gateway'
    area.mkdir(parents=True)
    source = area / 'module.py'
    source.write_text('value = 1\n')
    (area / 'AGENTS.md').write_text('Preserve guidance.\n')
    agent = _agent(tmp_path / 'egress')
    agent._current_api_request_id = 'turn-1:api:1'
    agent._subdirectory_hints = SubdirectoryHintTracker(str(area.parent))
    agent._touch_activity = lambda *_: None
    agent._tool_result_content_for_active_model = lambda _name, result: result
    agent.tool_progress_callback = None
    # UI/telemetry/persistence are outside this temporary tool-boundary fixture.
    monkeypatch.setattr(tool_executor, 'observe_onboarding_result', lambda _a, _n, _p, result: result)
    monkeypatch.setattr(tool_executor, 'note_tool_result', lambda *_: None)
    monkeypatch.setattr(tool_executor, 'get_active_env', lambda *_: None)
    monkeypatch.setattr(tool_executor, '_flush_session_db_after_tool_progress', lambda *_a, **_k: True)
    monkeypatch.setattr(tool_executor.lsp_deferred, 'forward_to_agent', lambda *_: None)
    monkeypatch.setattr(tool_executor.plugin_live_notes, 'attach_to_tool_message', lambda *_: None)
    with source_provenance_activation(agent, 'read_file'):
        result = read_file_tool(str(source), task_id='context-commit-read')
    messages = []
    ref = tool_executor._ToolCallRef('read_file', {'path': str(source)}, 'context-commit-read', 'call_commit', [])
    tool_executor._commit_tool_result(agent, messages, ref, result, budget=DEFAULT_BUDGET,
                                     tool_duration=0, is_error=False, blocked=False, effect_disposition=None)
    agent._current_api_request_id = 'turn-1:api:2'
    authorized, receipt = authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': messages})
    assert receipt.decision.source_grant_count == 2
    assert receipt.decision.source_segment_count == 2
    assert authorized['messages'][0]['content'] == messages[0]['content']


def test_guidance_from_another_workspace_cannot_cover_this_call(composite, tmp_path):
    agent, message, guidance = composite(denied=True)
    other = tmp_path / 'unrelated/gateway/AGENTS.md'
    other.parent.mkdir(parents=True)
    other.write_bytes(guidance.read_bytes())
    # This source really was read and verified, but belongs to another workspace/call.
    agent._source_provenance_registry.issue_file_slice(
        path=other, line_start=1, line_end=1, content=other.read_bytes(),
        session_id=agent.session_id, turn_id=agent._current_turn_id,
        request_id=agent._current_api_request_id, policy_digest=agent._llm_egress_policy_digest,
    )
    agent._current_api_request_id = 'turn-1:api:1'
    message['_source_provenance'] = attach_trusted_source_provenance_metadata(
        agent, 'read_file', content=message['content'],
    )
    agent._current_api_request_id = 'turn-1:api:2'
    with pytest.raises(EgressBlocked) as denied:
        authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [message]})
    assert 'untrusted_provenance' in denied.value.decision.reason_codes


@pytest.mark.parametrize('owner_change', ['session', 'policy', 'unvalidated_replay'])
def test_composite_cannot_rebind_an_unverified_or_different_owner(composite, owner_change):
    agent, message, _ = composite()
    if owner_change == 'session':
        agent.session_id = 'another-session'
    elif owner_change == 'policy':
        agent._llm_egress_policy_digest = 'f' * 64
    else:
        agent._current_api_request_id = 'turn-1:api:3'
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [message]})


def test_each_discovered_ancestor_guidance_retains_its_own_source_proof(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    root = tmp_path / 'repo'
    area = root / 'gateway/sub'
    area.mkdir(parents=True)
    (area.parent / 'AGENTS.md').write_text('Follow parent guidance.\n')
    (area / 'AGENTS.md').write_text('Follow nested guidance.\n')
    source = area / 'module.py'
    source.write_text('value = 1\n')
    agent = _agent(tmp_path / 'egress')
    agent._current_api_request_id = 'turn-1:api:1'
    tracker = SubdirectoryHintTracker(str(root))
    with source_provenance_activation(agent, 'read_file'):
        original = read_file_tool(str(source), task_id='nested-guidance-read')
        hints = tracker.check_tool_call('read_file', {'path': str(source)})
    combined = original + hints
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=combined)
    message = make_tool_result_message('read_file', combined, 'nested_call', source_provenance=metadata)
    agent._current_api_request_id = 'turn-1:api:2'
    authorized, receipt = authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [message]})
    assert receipt.decision.source_grant_count == 3
    assert receipt.decision.source_segment_count == 3
    assert authorized['messages'][0]['content'] == combined


def test_responses_sidecar_preserves_the_two_source_composite(composite):
    from agent.codex_responses_adapter import _chat_messages_to_responses_input
    from agent.source_provenance_tools import build_source_provenance_sidecar

    agent, message, _ = composite()
    agent.provider = 'openai-codex'
    agent.api_mode = 'codex_responses'
    messages = [{'role': 'assistant', 'tool_calls': [{
        'id': 'call_context', 'type': 'function',
        'function': {'name': 'read_file', 'arguments': '{}'},
    }]}, message]
    request = {'model': 'test-model', 'input': _chat_messages_to_responses_input(messages),
               '_hermes_source_provenance': build_source_provenance_sidecar(messages)}
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    assert receipt.decision.source_grant_count == 2
    assert receipt.decision.source_segment_count == 2
    assert authorized['input'][1]['output'] == message['content']
    assert '_source_provenance' not in authorized['input'][1]


@pytest.mark.parametrize('bom', [False, True])
@pytest.mark.parametrize('changed', [False, True])
def test_crlf_guidance_proof_keeps_raw_bytes_and_the_original_normalized_wire(tmp_path, monkeypatch, bom, changed):
    from hashlib import sha256

    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    root = tmp_path / 'repo'
    area = root / 'gateway'
    area.mkdir(parents=True)
    source = area / 'module.py'
    source.write_bytes(b'value = 1\n')
    guidance = area / 'AGENTS.md'
    raw = (b'\xef\xbb\xbf' if bom else b'') + b'Follow guidance.\r\nRun focused tests.\r\n'
    guidance.write_bytes(raw)
    agent = _agent(tmp_path / 'egress')
    agent._current_api_request_id = 'turn-1:api:1'
    tracker = SubdirectoryHintTracker(str(root))
    with source_provenance_activation(agent, 'read_file'):
        result = read_file_tool(str(source), task_id='crlf-guidance-read')
        hints = tracker.check_tool_call('read_file', {'path': str(source)})
    assert hints == '\n\n[Subdirectory context discovered: gateway/AGENTS.md]\nFollow guidance.\nRun focused tests.'
    combined = result + hints
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=combined)
    message = make_tool_result_message('read_file', combined, 'crlf_call', source_provenance=metadata)
    agent._current_api_request_id = 'turn-1:api:2'
    if changed:
        # Identical displayed text does not excuse a raw-byte change before first consumption.
        guidance.write_bytes(raw.replace(b'\r\n', b'\n'))
        with pytest.raises(EgressBlocked):
            authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [message]})
        return
    authorized, receipt = authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [message]})
    assert authorized['messages'][0]['content'] == combined
    assert receipt.decision.source_grant_count == 2
    granted = [grant for grant in agent._source_provenance_registry.grants_for_request('turn-1:api:2')
               if grant.canonical_path == guidance.resolve()]
    assert len(granted) == 1
    assert granted[0].byte_count == len(raw)
    assert granted[0].content_sha256 == sha256(raw).hexdigest()
