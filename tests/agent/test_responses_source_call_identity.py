"""Source producer identity survives actual Responses wire call-ID rewriting."""
import json

import pytest

from agent.codex_responses_adapter import _chat_messages_to_responses_input
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance_tools import build_source_provenance_sidecar, execute_with_source_provenance
from tests.agent.test_source_read_result_binding import authorize, produce
from tools.file_tools import read_file_tool
from tools.budget_config import DEFAULT_BUDGET


def second_read(agent, messages, path, call_id):
    from agent import tool_executor
    agent._current_api_request_id = 'turn-1:api:2'
    ref = tool_executor._ToolCallRef('read_file', {'path':str(path)}, 'producer-fixture', call_id, [])
    result = execute_with_source_provenance(agent, ref.name, ref.call_id,
        lambda args: read_file_tool(args['path'], task_id=ref.task_id), ref.args)
    tool_executor._commit_tool_result(agent, messages, ref, result, budget=DEFAULT_BUDGET,
        tool_duration=0, is_error=False, blocked=False, effect_disposition=None)


def chat_pair(message, path):
    return [{'role':'assistant', 'tool_calls':[{'id':message['tool_call_id'], 'type':'function',
             'function':{'name':'read_file', 'arguments':json.dumps({'path':str(path)})}}]}, message]


def formatted_request(tmp_path, monkeypatch, case):
    call_id = 'call_'+'long_identifier_'*8 if case == 'long' else 'call_repeated' if case == 'duplicate' else {'fc': 'fc_stored_item', 'split': 'call_original', 'whitespace': '  call_original  '}.get(case, 'call_short')
    agent, messages, paths = produce(tmp_path, monkeypatch, count=1, plain=True, call_ids=[call_id])
    chat = chat_pair(messages[0], paths[0])
    if case == 'split':
        chat[0]['tool_calls'][0]['id'] += '|fc_item'  # Dispatcher already canonicalizes the bridge ID.
    if case == 'duplicate':
        assert authorize(agent, messages, 2)[1].allowed
        second_path = paths[0].with_name('second_module.py')
        second_path.write_bytes(paths[0].read_bytes())
        second_read(agent, messages, second_path, call_id)
        chat += chat_pair(messages[1], second_path)
    agent.provider = 'openai-codex'
    agent.api_mode = 'codex_responses'
    agent._current_api_request_id = 'turn-1:api:3' if case == 'duplicate' else 'turn-1:api:2'
    sidecar = build_source_provenance_sidecar(chat)
    items = _chat_messages_to_responses_input(chat)
    return agent, {'model':'test-model', 'input':items, '_hermes_source_provenance':sidecar}, messages


@pytest.mark.parametrize('case', ['short', 'long', 'duplicate', 'fc', 'split', 'whitespace'])
def test_formatter_sidecar_and_authorizer_preserve_real_producer_occurrences(tmp_path, monkeypatch, case):
    agent, request, messages = formatted_request(tmp_path, monkeypatch, case)
    outputs = [item for item in request['input'] if item.get('type') == 'function_call_output']
    assert len({item['call_id'] for item in outputs}) == len(outputs)
    if case == 'long':
        assert outputs[0]['call_id'] != messages[0]['tool_call_id']
    if case == 'duplicate':
        assert outputs[1]['call_id'] == outputs[0]['call_id']+'_dup1'
        assert outputs[0]['output'] == outputs[1]['output']  # Content cannot identify occurrence.
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    authorized_outputs = [item for item in authorized['input'] if item.get('type') == 'function_call_output']
    assert [(item['call_id'], item['output']) for item in authorized_outputs] == [(item['call_id'], item['output']) for item in outputs]
    assert receipt.allowed


def output_items(request):
    body = request.get('extra_body', request)
    return [item for item in body['input'] if item.get('type') == 'function_call_output']


@pytest.mark.parametrize('case', ['long', 'duplicate'])
@pytest.mark.parametrize('normalize,bypass', [(True, False), (False, True), (True, True)])
def test_actual_preflight_and_consumer_bypass_keep_exact_occurrences(tmp_path, monkeypatch, case, normalize, bypass):
    from agent.codex_responses_adapter import _preflight_codex_input_items

    agent, request, _ = formatted_request(tmp_path, monkeypatch, case)
    original = [(item['call_id'], item['output']) for item in output_items(request)]
    if normalize:
        request['input'] = _preflight_codex_input_items(request['input'])
    if bypass:
        request['extra_body'] = {'input': request.pop('input')}
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    assert receipt.allowed
    assert [(item['call_id'], item['output']) for item in output_items(authorized)] == original


@pytest.mark.parametrize('change', ['call', 'body', 'position', 'sidecar', 'plain_copy', 'json_copy', 'collision'])
def test_correspondence_never_recovers_altered_or_unsealed_occurrences(tmp_path, monkeypatch, change):
    from agent.llm_egress_runtime import _restore_source_provenance_sidecar

    _, request, _ = formatted_request(tmp_path, monkeypatch, 'long')
    items = request['input']
    position = next(index for index, item in enumerate(items) if item.get('type') == 'function_call_output')
    actions = {
        'call': lambda: items[position].__setitem__('call_id', 'call_swapped'),
        'body': lambda: items[position].__setitem__('output', items[position]['output'] + ' altered'),
        'position': lambda: items.insert(0, {'role': 'user', 'content': 'unrelated'}),
        'sidecar': lambda: request['_hermes_source_provenance'][0].__setitem__('read_result_binding_id', 'unknown'),
        'plain_copy': lambda: request.__setitem__('input', list(items)),
        'json_copy': lambda: request.__setitem__('input', json.loads(json.dumps(items))),
        'collision': lambda: items.append(dict(items[position])),
    }
    actions[change]()
    restored = _restore_source_provenance_sidecar(request, request['_hermes_source_provenance'])
    assert all('_source_provenance' not in item for item in restored['input'])


@pytest.mark.parametrize('change', ['session', 'turn', 'policy', 'earlier_request', 'source'])
def test_protocol_correspondence_does_not_replace_registry_authority(tmp_path, monkeypatch, change):
    from agent.llm_egress_firewall import EgressBlocked

    agent, request, _ = formatted_request(tmp_path, monkeypatch, 'long')
    from pathlib import Path
    call = next(item for item in request['input'] if item.get('type') == 'function_call')
    path = Path(json.loads(call['arguments'])['path'])
    actions = {
        'session': lambda: setattr(agent, 'session_id', 'different-owner'),
        'turn': lambda: setattr(agent, '_current_turn_id', 'different-turn'),
        'policy': lambda: setattr(agent, '_llm_egress_policy_digest', 'different-policy'),
        'earlier_request': lambda: setattr(agent, '_current_api_request_id', 'turn-1:api:1'),
        'source': lambda: path.write_text('altered = True\n'),
    }
    actions[change]()
    try:
        authorized, _ = authorize_agent_sdk_kwargs(agent, request)
    except EgressBlocked:
        return
    from agent.llm_egress_terminal import _READ_FILE_REPLAY_ELISION
    assert [item['output'] for item in output_items(authorized)] == [_READ_FILE_REPLAY_ELISION]


def test_real_compaction_pruning_preserves_final_retained_output_position(tmp_path, monkeypatch):
    agent, request, messages = formatted_request(tmp_path, monkeypatch, 'long')
    path = json.loads(next(item for item in request['input'] if item.get('type') == 'function_call')['arguments'])['path']
    chat = [{'role': 'user', 'content': 'old input'},
            {'role': 'assistant', 'content': '', 'codex_reasoning_items': [
                {'type': 'compaction', 'encrypted_content': 'opaque-checkpoint'}]}]
    chat += chat_pair(messages[0], path)
    request['input'] = _chat_messages_to_responses_input(chat, native_compaction_eligible=True)
    request['_hermes_source_provenance'] = build_source_provenance_sidecar(chat)
    assert request['input'][0]['type'] == 'compaction'
    original = [(item['call_id'], item['output']) for item in output_items(request)]
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    assert receipt.allowed
    assert [(item['call_id'], item['output']) for item in output_items(authorized)] == original


def test_formatter_natural_suffix_collision_does_not_create_correspondence(tmp_path, monkeypatch):
    from agent.llm_egress_runtime import _restore_source_provenance_sidecar

    _, _, messages = formatted_request(tmp_path, monkeypatch, 'duplicate')
    chat = chat_pair(messages[0], 'module.py') + chat_pair(messages[1], 'second.py')
    natural = {**messages[1], 'tool_call_id': 'call_repeated_dup1'}
    chat += chat_pair(natural, 'third.py')
    items = _chat_messages_to_responses_input(chat)
    sidecar = build_source_provenance_sidecar(chat)
    restored = _restore_source_provenance_sidecar({'input': items}, sidecar)
    collisions = [item for item in restored['input'] if item.get('call_id') == 'call_repeated_dup1'
                  and item.get('type') == 'function_call_output']
    assert len(collisions) == 2
    assert all('_source_provenance' not in item for item in collisions)


@pytest.mark.parametrize('admitted', [False, True])
def test_formatter_mapping_does_not_create_historical_admission(tmp_path, monkeypatch, admitted):
    from agent.llm_egress_terminal import _READ_FILE_REPLAY_ELISION

    agent, request, _ = formatted_request(tmp_path, monkeypatch, 'long')
    original = [(item['call_id'], item['output']) for item in output_items(request)]
    if admitted:
        authorize_agent_sdk_kwargs(agent, request)
    agent._current_api_request_id = 'turn-1:api:3'
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    assert receipt.allowed
    if admitted:
        assert [(item['call_id'], item['output']) for item in output_items(authorized)] == original
    else:
        assert [item['output'] for item in output_items(authorized)] == [_READ_FILE_REPLAY_ELISION]


def test_sidecar_cannot_supply_a_forged_original_label(tmp_path, monkeypatch):
    from agent.source_read_results import observed_source_metadata, OBSERVED_CALL_ID

    _, request, _ = formatted_request(tmp_path, monkeypatch, 'long')
    item = output_items(request)[0]
    forged = {**item, '_source_provenance': {**request['_hermes_source_provenance'][0],
              '_responses_source_identity': {'original_call_id': 'call_forged'}}}
    assert observed_source_metadata(forged)[OBSERVED_CALL_ID] == item['call_id']


def test_duplicate_binding_entries_refuse_ambiguous_correspondence(tmp_path, monkeypatch):
    from agent.llm_egress_runtime import _restore_source_provenance_sidecar

    _, request, _ = formatted_request(tmp_path, monkeypatch, 'long')
    request['input'].bindings *= 2
    restored = _restore_source_provenance_sidecar(request, request['_hermes_source_provenance'])
    assert all('_source_provenance' not in item for item in restored['input'])
