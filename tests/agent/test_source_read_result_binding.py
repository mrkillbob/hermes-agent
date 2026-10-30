"""Actual dispatcher read/commit results retain exact call and source identities."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json

import pytest

from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance_tools import execute_with_source_provenance, build_source_provenance_sidecar
from agent.source_read_results import READ_RESULT_ID
from agent.subdirectory_hints import SubdirectoryHintTracker
from tests.agent.test_llm_egress_runtime import _agent
from tools.budget_config import DEFAULT_BUDGET
from tools.file_tools import read_file_tool


def produce(tmp_path, monkeypatch, *, count=2, same_directory=False, plain=False, duplicate=False, parallel=False, source_equals_guidance=False, guidance_override=None, call_ids=None):
    from agent import tool_executor

    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    root = tmp_path / 'repo'
    root.mkdir(parents=True)
    agent = _agent(tmp_path/'egress')
    agent._current_api_request_id = 'turn-1:api:1'
    agent._subdirectory_hints = SubdirectoryHintTracker(str(root))
    agent._touch_activity = lambda *_: None
    agent._tool_result_content_for_active_model = lambda _name, result: result
    agent.tool_progress_callback = None
    # Only UI, telemetry and session persistence are stubbed; read/commit/egress are real.
    monkeypatch.setattr(tool_executor, 'observe_onboarding_result', lambda _a, _n, _p, result: result)
    monkeypatch.setattr(tool_executor, 'note_tool_result', lambda *_: None)
    monkeypatch.setattr(tool_executor, 'get_active_env', lambda *_: None)
    monkeypatch.setattr(tool_executor, '_flush_session_db_after_tool_progress', lambda *_a, **_k: True)
    monkeypatch.setattr(tool_executor.lsp_deferred, 'forward_to_agent', lambda *_: None)
    monkeypatch.setattr(tool_executor.plugin_live_notes, 'attach_to_tool_message', lambda *_: None)
    paths, refs = [], []
    for index in range(count):
        area = root / ('gateway' if same_directory else f'gateway-{index}')
        area.mkdir(exist_ok=True)
        source = area / f'module-{index}.py'
        source.write_text('Preserve guidance 0.\n' if source_equals_guidance else 'value = 1\n')
        if not plain:
            (area/'AGENTS.md').write_text(guidance_override if guidance_override is not None else f'Preserve guidance {0 if same_directory else index}.\n')
        call_id = call_ids[index] if call_ids is not None else 'call_duplicate' if duplicate else f'call_producer_{index}'
        refs.append(tool_executor._ToolCallRef('read_file', {'path':str(source)}, 'producer-fixture', call_id, []))
        paths.append(source)

    def read(ref):
        return execute_with_source_provenance(
            agent, ref.name, ref.call_id,
            lambda arguments: read_file_tool(arguments['path'], task_id=ref.task_id), ref.args)

    if parallel:
        with ThreadPoolExecutor(max_workers=min(count, 4)) as executor:
            results = list(executor.map(read, refs))
    else:
        results = [read(ref) for ref in refs]
    messages = []
    for ref, result in zip(refs, results):
        tool_executor._commit_tool_result(agent, messages, ref, result, budget=DEFAULT_BUDGET,
                                         tool_duration=0, is_error=False, blocked=False, effect_disposition=None)
    return agent, messages, paths


def authorize(agent, messages, number=2, **kwargs):
    agent._current_api_request_id = f'turn-1:api:{number}'
    return authorize_agent_sdk_kwargs(agent, {'model':'test-model', 'messages':messages, **kwargs})


@pytest.mark.parametrize('same_directory', [False, True])
@pytest.mark.parametrize('plain', [False, True])
@pytest.mark.parametrize('parallel', [False, True])
def test_exact_producer_identity_survives_real_commit_and_history(tmp_path, monkeypatch, same_directory, plain, parallel):
    agent, messages, _ = produce(tmp_path, monkeypatch, same_directory=same_directory, plain=plain, parallel=parallel)
    expected = [message['content'] for message in messages]
    proofs = 2 if plain else 3 if same_directory else 4
    for number in (2, 3, 4):
        authorized, receipt = authorize(agent, messages, number)
        assert [message['content'] for message in authorized['messages']] == expected
        assert receipt.decision.source_grant_count == proofs
        assert all('_source_provenance' not in message for message in authorized['messages'])


@pytest.mark.parametrize('case', ['swapped', 'unknown', 'malformed', 'missing_call', 'altered_call', 'spoofed_observed', 'changed_wire', 'missing_original'])
def test_explicit_result_binding_cannot_move_or_fall_back(tmp_path, monkeypatch, case):
    agent, messages, _ = produce(tmp_path, monkeypatch)
    first = messages[0]
    metadata = first['_source_provenance']

    def changed_wire():
        first['content'] = first['content'].replace('Preserve guidance 0.', 'Preserve different guidance.')
        metadata['content_sha256'] = sha256(first['content'].encode()).hexdigest()

    def spoofed_observed():
        metadata['_observed_tool_call_id'] = first['tool_call_id']
        first['tool_call_id'] = 'call_wrong'

    actions = {'swapped':lambda: metadata.update({READ_RESULT_ID:messages[1]['_source_provenance'][READ_RESULT_ID]}),
               'unknown':lambda: metadata.update({READ_RESULT_ID:'0'*32}),
               'malformed':lambda: metadata.update({READ_RESULT_ID:None}),
               'missing_call':lambda: first.pop('tool_call_id'),
               'altered_call':lambda: first.update(tool_call_id='call_wrong'),
               'spoofed_observed':spoofed_observed, 'changed_wire':changed_wire,
               'missing_original':lambda: metadata.update(source_grant_digests=[])}
    actions[case]()
    with pytest.raises(EgressBlocked):
        authorize(agent, messages)


@pytest.mark.parametrize('case', ['session', 'turn', 'policy', 'request', 'unadmitted', 'duplicate', 'duplicate_message'])
def test_call_and_history_ownership_remain_strict(tmp_path, monkeypatch, case):
    agent, messages, _ = produce(tmp_path, monkeypatch, duplicate=case == 'duplicate')
    actions = {'session':lambda: setattr(agent, 'session_id', 'different-owner'),
               'turn':lambda: setattr(agent, '_current_turn_id', 'different-turn'),
               'policy':lambda: setattr(agent, '_llm_egress_policy_digest', 'different-policy'),
               'request':lambda: messages[0]['_source_provenance'].update(request_id='turn-1:api:8'),
               'unadmitted':lambda: None, 'duplicate':lambda: None,
               'duplicate_message':lambda: messages.append(messages[0])}
    actions[case]()
    with pytest.raises(EgressBlocked):
        authorize(agent, messages, 3 if case == 'unadmitted' else 2)


@pytest.mark.parametrize('case', ['changed', 'denied', 'clear_request', 'clear_turn', 'fresh_identical', 'failed_admission'])
def test_replay_rechecks_source_and_requires_original_complete_admission(tmp_path, monkeypatch, case):
    agent, messages, paths = produce(tmp_path, monkeypatch)
    registry = agent._source_provenance_registry

    def denied():
        import agent.source_provenance as provenance
        original = provenance.get_read_block_error
        monkeypatch.setattr(provenance, 'get_read_block_error',
                            lambda path: 'denied' if str(path) == str(paths[0]) else original(path))

    def fresh_identical():
        registry.clear_turn('turn-1')
        agent._current_api_request_id = 'turn-1:api:2'
        execute_with_source_provenance(agent, 'read_file', 'call_fresh',
                                      lambda args: read_file_tool(args['path'], task_id='fresh-fixture'), {'path':str(paths[0])})

    def failed_admission():
        from hermes_cli import env_loader
        home = tmp_path/'profile-home'
        home.mkdir()
        monkeypatch.setenv('HERMES_HOME', str(home))
        monkeypatch.setitem(env_loader._SECRET_SOURCE_VALUES_BY_HOME, str(home.resolve()), {'EXTERNAL_VALUE':'Preserve guidance 0.'})
        with pytest.raises(EgressBlocked):
            authorize(agent, messages)
        monkeypatch.setitem(env_loader._SECRET_SOURCE_VALUES_BY_HOME, str(home.resolve()), {})

    if case not in ('clear_request', 'failed_admission'):
        authorize(agent, messages)
    actions = {'changed':lambda: paths[0].write_text('value = 2\n'), 'denied':denied,
               'clear_request':lambda: registry.clear_request('turn-1:api:2'),
               'clear_turn':lambda: registry.clear_turn('turn-1'), 'fresh_identical':fresh_identical,
               'failed_admission':failed_admission}
    actions[case]()
    with pytest.raises(EgressBlocked):
        authorize(agent, messages, 3)


def test_admitted_result_survives_request_cleanup_but_not_turn_cleanup(tmp_path, monkeypatch):
    agent, messages, _ = produce(tmp_path, monkeypatch)
    authorize(agent, messages)
    registry = agent._source_provenance_registry
    registry.clear_request('turn-1:api:2')
    authorized, _ = authorize(agent, messages, 3)
    assert [value['content'] for value in authorized['messages']] == [value['content'] for value in messages]
    registry.clear_turn('turn-1')
    with pytest.raises(EgressBlocked):
        authorize(agent, messages, 4)


def test_another_agent_cannot_borrow_identical_owner_labels_and_wire(tmp_path, monkeypatch):
    first, messages, _ = produce(tmp_path/'first', monkeypatch, plain=True)
    second, other_messages, _ = produce(tmp_path/'second', monkeypatch, plain=True)
    assert messages[0]['content'] == other_messages[0]['content']
    messages[0]['_source_provenance'][READ_RESULT_ID] = other_messages[0]['_source_provenance'][READ_RESULT_ID]
    with pytest.raises(EgressBlocked):
        authorize(first, messages)
    assert authorize(second, other_messages)[1].allowed


@pytest.mark.parametrize('wire', ['chat', 'responses', 'native_anthropic', 'native_nous', 'native_custom'])
def test_exact_binding_survives_real_sidecar_wire_roundtrip(tmp_path, monkeypatch, wire):
    agent, messages, paths = produce(tmp_path, monkeypatch)
    calls = [{'id':value['tool_call_id'], 'type':'function',
              'function':{'name':'read_file', 'arguments':json.dumps({'path':str(path)})}}
             for value, path in zip(messages, paths)]
    chat = [{'role':'assistant', 'tool_calls':calls}, *messages]
    sidecar = build_source_provenance_sidecar(chat)
    chat_wire = [{key:value for key, value in message.items() if key != '_source_provenance'} for message in chat]
    native = [
        {'role':'assistant', 'content':[{'type':'tool_use', 'id':call['id'], 'name':'read_file', 'input':{}}
                                      for call in calls]},
        {'role':'user', 'content':[{'type':'tool_result', 'tool_use_id':message['tool_call_id'], 'content':message['content']}
                                 for message in messages]},
    ]
    from agent.codex_responses_adapter import _chat_messages_to_responses_input
    requests = {'chat':{'messages':chat_wire}, 'responses':{'input':_chat_messages_to_responses_input(chat)},
                'native':{'messages':native}}
    if wire == 'chat':
        agent.provider = 'nous'
    if wire.startswith('native_'):
        agent.provider = wire.removeprefix('native_')
    if wire == 'responses':
        agent.provider = 'openai-codex'
        agent.api_mode = 'codex_responses'
    agent._current_api_request_id = 'turn-1:api:2'
    request = {'model':'test-model', **requests['native' if wire.startswith('native_') else wire],
               '_hermes_source_provenance':sidecar}
    if wire == 'native_custom':
        with pytest.raises(EgressBlocked):
            authorize_agent_sdk_kwargs(agent, request)
        return  # Existing unsupported-route refusal is preserved.
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    encoded = json.dumps(authorized)
    assert all(json.dumps(message['content'])[1:-1] in encoded for message in messages)
    assert receipt.decision.source_grant_count == 4
    assert READ_RESULT_ID not in encoded and '_source_provenance' not in encoded


def test_custom_chat_does_not_export_private_call_arguments(tmp_path, monkeypatch):
    agent, messages, _ = produce(tmp_path, monkeypatch, plain=True)
    private_path = '/var/tmp/private-project-source.py'
    calls = [{'id': message['tool_call_id'], 'type': 'function',
              'function': {'name': 'read_file', 'arguments': json.dumps({'path': private_path})}}
             for message in messages]
    chat = [{'role': 'assistant', 'tool_calls': calls}, *messages]
    # Path text is test data only: no file at this private path is opened.
    with pytest.raises(EgressBlocked) as refused:
        authorize(agent, chat)
    assert 'private_absolute_path' in refused.value.decision.reason_codes


@pytest.mark.parametrize('count', [17, 32])
def test_identical_prefix_batches_replay_within_unchanged_real_grant_cap(tmp_path, monkeypatch, count):
    agent, messages, _ = produce(tmp_path, monkeypatch, count=count)
    for number in (2, 3, 4):
        authorized, receipt = authorize(agent, messages, number)
        assert [value['content'] for value in authorized['messages']] == [value['content'] for value in messages]
        assert receipt.decision.source_grant_count == count*2
        assert len(agent._source_provenance_registry.grants_for_request(f'turn-1:api:{number}')) == count*2


def test_producer_results_cannot_bypass_existing_registry_capacity(tmp_path, monkeypatch):
    from agent.source_provenance import MAX_GRANTS_PER_REQUEST
    agent, messages, _ = produce(tmp_path, monkeypatch, count=MAX_GRANTS_PER_REQUEST+1, plain=True)
    assert len(agent._source_provenance_registry.grants_for_request('turn-1:api:2')) == MAX_GRANTS_PER_REQUEST
    with pytest.raises(EgressBlocked):
        authorize(agent, messages)


def test_producer_record_bytes_are_bounded_without_relaxing_file_proofs(tmp_path, monkeypatch):
    import agent.source_provenance as provenance
    monkeypatch.setattr(provenance, 'MAX_SOURCE_SLICE_BYTES', 100)
    agent, messages, _ = produce(tmp_path, monkeypatch, plain=True)
    assert len(agent._source_provenance_registry.grants_for_request('turn-1:api:2')) == 2
    assert all(message['_source_provenance'][READ_RESULT_ID] is None for message in messages)
    with pytest.raises(EgressBlocked):
        authorize(agent, messages)


def test_prefix_equal_to_neighbor_guidance_still_selects_its_actual_source(tmp_path, monkeypatch):
    agent, messages, paths = produce(tmp_path, monkeypatch, source_equals_guidance=True)
    assert paths[1].read_bytes() == (paths[0].parent/'AGENTS.md').read_bytes()
    for number in (2, 3):
        authorized, receipt = authorize(agent, messages, number)
        assert [value['content'] for value in authorized['messages']] == [value['content'] for value in messages]
        assert receipt.decision.source_grant_count == 4


@pytest.mark.parametrize('mutation', ['call', 'reference', 'duplicate'])
def test_native_sidecar_binding_mismatch_cannot_admit_original_result_history(tmp_path, monkeypatch, mutation):
    from agent.llm_egress_runtime import _restore_source_provenance_sidecar
    agent, messages, _ = produce(tmp_path, monkeypatch)
    agent.provider = 'anthropic'
    sidecar = build_source_provenance_sidecar(messages)
    native = [{'role':'assistant', 'content':[{'type':'tool_use', 'id':message['tool_call_id'], 'name':'read_file', 'input':{}}
                                             for message in messages]},
              {'role':'user', 'content':[{'type':'tool_result', 'tool_use_id':message['tool_call_id'], 'content':message['content']}
                                        for message in messages]}]
    actions = {'call':lambda: native[1]['content'][0].update(tool_use_id='call_wrong'),
               'reference':lambda: sidecar[0].update({READ_RESULT_ID:sidecar[1][READ_RESULT_ID]}),
               'duplicate':lambda: sidecar.append(dict(sidecar[0]))}
    actions[mutation]()
    restored = _restore_source_provenance_sidecar({'messages':native}, sidecar)
    if mutation == 'call':
        assert '_source_provenance' not in restored['messages'][1]['content'][0]
    agent._current_api_request_id = 'turn-1:api:2'
    try:
        authorized, receipt = authorize_agent_sdk_kwargs(
            agent, {'model':'test-model', 'messages':native, '_hermes_source_provenance':sidecar})
    except EgressBlocked:
        pass
    else:
        assert messages[0]['content'] not in json.dumps(authorized)
        assert receipt.decision.source_grant_count < 4
    with pytest.raises(EgressBlocked):
        authorize(agent, messages, 3)
