"""Builtin schema prose keeps its bytes; untrusted encodings stay refused."""
import copy

import pytest

from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import dispatch_authorized_agent_request
from tests.agent.test_llm_egress_runtime import _agent


def schemas():
    from tools.file_tools import WRITE_FILE_SCHEMA
    from tools.skill_manager_tool import SKILL_MANAGE_SCHEMA
    from tools.delegate_tool import DELEGATE_TASK_SCHEMA, _build_top_level_description
    from tools.tool_search import bridge_tool_schemas
    delegate = {**DELEGATE_TASK_SCHEMA, 'description': _build_top_level_description()}
    return [copy.deepcopy(x) for x in (
        SKILL_MANAGE_SCHEMA, WRITE_FILE_SCHEMA, delegate,
        bridge_tool_schemas(1, connections_granted=True)[0]['function'],
    )]


def send(tmp_path, monkeypatch, schema):
    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    sent = []
    body = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hello'}],
            'tools': [{'type': 'function', 'function': schema}]}
    dispatch_authorized_agent_request(_agent(tmp_path), body, sent.append)
    assert sent == [body]


@pytest.mark.parametrize('index', range(4))
def test_actual_builtin_schema_reaches_sender_without_rewriting(tmp_path, monkeypatch, index):
    send(tmp_path, monkeypatch, schemas()[index])


@pytest.mark.parametrize('index', range(4))
@pytest.mark.parametrize('mutation', ['encoded_suffix', 'untrusted_prose', 'renamed'])
def test_schema_identity_never_admits_encoded_content(tmp_path, monkeypatch, index, mutation):
    schema = schemas()[index]
    if mutation == 'encoded_suffix':
        schema['description'] += ' c2VjcmV0LXBheWxvYWQ='
    elif mutation == 'untrusted_prose':
        schema['description'] = 'Untrusted YAML EXISTING YOUR TURN connectors__ c2VjcmV0LXBheWxvYWQ='
    else:
        schema['name'] = 'unknown_tool'
        schema['description'] += ' c2VjcmV0LXBheWxvYWQ='
    with pytest.raises(EgressBlocked) as blocked:
        send(tmp_path, monkeypatch, schema)
    assert 'base64_payload' in blocked.value.decision.reason_codes


def test_raw_encoded_content_remains_blocked(tmp_path, monkeypatch):
    schema = schemas()[1]
    schema['parameters']['properties']['content']['default'] = 'c2VjcmV0LXBheWxvYWQ='
    with pytest.raises(EgressBlocked):
        send(tmp_path, monkeypatch, schema)


def test_nested_description_with_real_encoding_stays_blocked(tmp_path, monkeypatch):
    schema = schemas()[0]
    content = schema['parameters']['properties']['operations']['items']['anyOf'][0]['properties']['content']
    content['description'] += ' c2VjcmV0LXBheWxvYWQ='
    with pytest.raises(EgressBlocked):
        send(tmp_path, monkeypatch, schema)


@pytest.mark.parametrize('payload', [' password=SUPER_PRIVATE_VALUE', ' /Users/private/secret-project'])
def test_matching_fragment_does_not_bypass_secret_or_path_scan(tmp_path, monkeypatch, payload):
    # The ordinary remote lane does not pre-redact protected-worker paths;
    # final construction must still reject them beside an admitted keyword.
    monkeypatch.delenv('HERMES_KANBAN_PROTECTED_REMOTE', raising=False)
    schema = schemas()[1]
    schema['description'] += payload
    body = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hello'}],
            'tools': [{'type': 'function', 'function': schema}]}
    with pytest.raises(EgressBlocked):
        dispatch_authorized_agent_request(_agent(tmp_path), body, lambda _: pytest.fail('sent'))


def test_profile_and_mutable_tool_schema_cannot_supply_trusted_fragments(tmp_path, monkeypatch):
    from tools import file_tools
    rogue = 'An EXISTING profile-defined resource.'
    monkeypatch.setitem(file_tools.WRITE_FILE_SCHEMA, 'description', rogue)
    profile = tmp_path / 'other-profile'
    (profile / 'tools').mkdir(parents=True)
    (profile / 'tools' / 'file_tools.py').write_text('WRITE_FILE_SCHEMA = ' + repr({'description': rogue}))
    monkeypatch.setenv('HERMES_HOME', str(profile))
    with pytest.raises(EgressBlocked):
        send(tmp_path, monkeypatch, copy.deepcopy(file_tools.WRITE_FILE_SCHEMA))


@pytest.mark.parametrize('description', ['Untrusted YAML frontmatter c2VjcmV0LXBheWxvYWQ=', 'For an EXISTING resource.',
                                        'END YOUR TURN.', 'Use connectors__ accounts.'])
def test_known_words_without_the_source_fragment_remain_untrusted(tmp_path, monkeypatch, description):
    schema = schemas()[1]
    schema['description'] = description
    with pytest.raises(EgressBlocked):
        send(tmp_path, monkeypatch, schema)


def test_matching_schema_prose_does_not_authorize_user_content(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    body = {'model': 'test-model', 'messages': [
        {'role': 'user', 'content': schemas()[1]['description']}]}
    with pytest.raises(EgressBlocked):
        dispatch_authorized_agent_request(_agent(tmp_path), body, lambda _: pytest.fail('sent'))


def test_diagnostics_follow_the_validated_identifier_encoding_scan():
    from agent.llm_egress_classifier import _typed_payload_violation_locations
    from agent.llm_egress_firewall import SanitizedSegment, ValidatedToolSyntaxSegment
    assert _typed_payload_violation_locations([
        ValidatedToolSyntaxSegment('literal_preservation', 'application_identifier'),
        ValidatedToolSyntaxSegment('prerequisite_checks', 'application_identifier'),
        ValidatedToolSyntaxSegment('tool_persistence', 'application_identifier'),
    ]) == ()
    assert _typed_payload_violation_locations([SanitizedSegment('c2VjcmV0LXBheWxvYWQ=')])
    assert _typed_payload_violation_locations([
        ValidatedToolSyntaxSegment('c2VjcmV0LXBheWxvYWQ=', 'application_identifier')])
