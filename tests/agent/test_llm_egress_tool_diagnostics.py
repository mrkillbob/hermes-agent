"""Application diagnostic replay retains exact-call and content boundaries."""

import json

import pytest

from agent.conversation_loop import _invalid_tool_name_error_content
from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance_tools import (
    attach_trusted_source_provenance_metadata,
    source_provenance_activation,
)
from agent.tool_dispatch_helpers import make_tool_result_message
from tests.agent.test_llm_egress_runtime import _agent
from tools.file_tools import read_file_tool


def frame(name, content, call_id="call_one"):
    return {
        "model": "test-model",
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "name": name, "tool_call_id": call_id, "content": content},
        ],
    }


def test_actual_invalid_tool_diagnostic_preserves_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent = _agent(tmp_path)
    agent.valid_tool_names = {"terminal", "read_file", "kanban_show"}
    content = _invalid_tool_name_error_content("shell", agent.valid_tool_names)
    body = frame("shell", content)
    actual, receipt = authorize_agent_sdk_kwargs(agent, body)
    assert actual == body
    assert receipt.decision.source_grant_count == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "suffix",
        "mismatch",
        "reordered",
        "duplicate",
        "catalog",
        "user",
        "encoded_name",
        "unpadded_name",
        "label",
        "result_duplicate",
        "secret",
        "path",
    ],
)
def test_invalid_diagnostic_cannot_admit_untrusted_text(
    tmp_path, monkeypatch, mutation
):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent = _agent(tmp_path)
    agent.valid_tool_names = {"terminal", "read_file"}
    name = {
        "encoded_name": "SGVsbG8gd29ybGQ=",
        "unpadded_name": "SGVsbG8gd29ybGQ",
        "known_name": "terminal",
    }.get(mutation, "shell")
    body = frame(name, _invalid_tool_name_error_content(name, agent.valid_tool_names))
    messages = body["messages"]
    result = messages[-1]

    def append(text):
        result.update(content=result["content"] + text)

    actions = {
        "suffix": lambda: append(" SGVsbG8gd29ybGQ="),
        "mismatch": lambda: result.update(tool_call_id="call_other"),
        "reordered": messages.reverse,
        "duplicate": lambda: messages[0]["tool_calls"].append(
            dict(messages[0]["tool_calls"][-1])
        ),
        "catalog": lambda: result.update(
            content=_invalid_tool_name_error_content(name, {"terminal", "attacker"})
        ),
        "label": lambda: result.update(name="other"),
        "result_duplicate": lambda: messages.append(dict(result)),
        "secret": lambda: append(" sk-proj-" + "x" * 48),
        "path": lambda: append(" /Users/private/credential.txt"),
        "user": lambda: result.update(
            role="user", content=result["content"] + " SGVsbG8gd29ybGQ="
        ),
    }
    actions.get(mutation, lambda: None)()
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, body)


def empty_read_frame(tmp_path):
    agent = _agent(tmp_path / "egress")
    agent.valid_tool_names = {"read_file", "terminal"}
    agent._current_api_request_id = "turn-1:api:1"
    source = tmp_path / "source.txt"
    source.write_text("public line\n", encoding="utf-8")
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    with source_provenance_activation(agent, "read_file"):
        first = read_file_tool(str(source), task_id="diagnostic-read")
    first_metadata = attach_trusted_source_provenance_metadata(
        agent, "read_file", content=first
    )
    with source_provenance_activation(agent, "read_file"):
        result = read_file_tool(str(empty), task_id="diagnostic-read")
    metadata = attach_trusted_source_provenance_metadata(
        agent, "read_file", content=result
    )
    message = make_tool_result_message(
        "read_file", result, "call_one", source_provenance=metadata
    )
    body = frame("read_file", result)
    body["messages"][-1] = message
    first_call = {
        "id": "call_source",
        "type": "function",
        "function": {"name": "read_file", "arguments": "{}"},
    }
    body["messages"][0]["tool_calls"].insert(0, first_call)
    body["messages"].insert(
        1,
        make_tool_result_message(
            "read_file", first, "call_source", source_provenance=first_metadata
        ),
    )
    agent._current_api_request_id = "turn-1:api:2"
    return agent, body, result


def test_actual_empty_read_after_nonempty_batch_read_preserves_outcome(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent, body, result = empty_read_frame(tmp_path)
    assert json.loads(result)["file_size"] == 0
    actual, receipt = authorize_agent_sdk_kwargs(agent, body)
    assert actual["messages"][-1]["content"] == result
    assert receipt.decision.source_segment_count == 1
    assert receipt.decision.source_grant_count == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "content",
        "hint",
        "extra",
        "mismatch",
        "duplicate",
        "reordered",
        "label",
        "typed_number",
        "duplicate_key",
        "secret",
        "path",
    ],
)
def test_empty_read_cannot_launder_modified_result(tmp_path, monkeypatch, mutation):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent, body, result = empty_read_frame(tmp_path)
    messages = body["messages"]
    message = messages[-1]

    def change_field(key, value):
        parsed = json.loads(result)
        parsed[key] = value
        message["content"] = json.dumps(parsed)

    actions = {
        "content": lambda: change_field("content", "SGVsbG8gd29ybGQ="),
        "hint": lambda: change_field("hint", "SGVsbG8gd29ybGQ="),
        "extra": lambda: change_field("other", "SGVsbG8gd29ybGQ="),
        "mismatch": lambda: message.update(tool_call_id="call_other"),
        "duplicate": lambda: messages[0]["tool_calls"].append(
            dict(messages[0]["tool_calls"][-1])
        ),
        "label": lambda: message.update(tool_name="other"),
        "typed_number": lambda: change_field("file_size", False),
        "duplicate_key": lambda: message.update(
            content=result[:-1] + ', "content": "SGVsbG8gd29ybGQ="}'
        ),
        "secret": lambda: message.update(content=result + " sk-proj-" + "x" * 48),
        "path": lambda: message.update(
            content=result + " /Users/private/credential.txt"
        ),
        "reordered": messages.reverse,
    }
    actions[mutation]()
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, body)


@pytest.mark.parametrize("enabled", [True, False])
def test_registered_tool_diagnostic_does_not_gain_authority(tmp_path, enabled):
    from agent.llm_egress_firewall import UntrustedProvenanceSegment
    from agent.llm_egress_tool_diagnostics import type_tool_diagnostics

    agent = _agent(tmp_path)
    agent.valid_tool_names = {"terminal", "read_file"}
    body = frame(
        "terminal", _invalid_tool_name_error_content("terminal", agent.valid_tool_names)
    )
    typed = {"messages": [{}, {"content": UntrustedProvenanceSegment("0" * 64)}]}
    assert type_tool_diagnostics(agent, body, typed, enabled) == typed


@pytest.mark.parametrize(
    "enabled,available", [(False, {"terminal"}), (True, None), (True, set())]
)
def test_diagnostic_requires_enabled_owner_catalog(tmp_path, enabled, available):
    from agent.llm_egress_firewall import UntrustedProvenanceSegment
    from agent.llm_egress_tool_diagnostics import type_tool_diagnostics

    agent = _agent(tmp_path)
    agent.valid_tool_names = available
    body = frame("shell", _invalid_tool_name_error_content("shell", {"terminal"}))
    typed = {"messages": [{}, {"content": UntrustedProvenanceSegment("0" * 64)}]}
    assert type_tool_diagnostics(agent, body, typed, enabled) == typed
