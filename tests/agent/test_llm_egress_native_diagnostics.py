"""Native message conversion must retain exact diagnostic-result provenance."""

import copy

import pytest

from agent.anthropic_message_convert import convert_messages_to_anthropic
from agent.conversation_loop import _invalid_tool_name_error_content
from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from tests.agent.test_llm_egress_runtime import _agent
from tests.agent.test_llm_egress_tool_diagnostics import frame


def native_frame(name, content):
    _, messages = convert_messages_to_anthropic(frame(name, content)["messages"])
    assert any(
        block.get("type") == "tool_use"
        for m in messages
        if isinstance(m.get("content"), list)
        for block in m["content"]
    )
    assert any(
        block.get("type") == "tool_result"
        for m in messages
        if isinstance(m.get("content"), list)
        for block in m["content"]
    )
    return {"model": "test-model", "messages": messages}


@pytest.mark.parametrize("transport", ["openai", "anthropic"])
@pytest.mark.parametrize("name", ["shell", "", "   "])
def test_actual_name_correction_preserved_after_native_conversion(
    tmp_path, monkeypatch, transport, name
):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent = _agent(tmp_path)
    agent.valid_tool_names = {"read_file", "terminal"}
    content = _invalid_tool_name_error_content(name, agent.valid_tool_names)
    build = native_frame if transport == "anthropic" else frame
    body = build(name, content)
    if transport == "anthropic":
        agent.provider = "anthropic"
        agent.api_mode = "anthropic_messages"
        agent.base_url = "https://api.anthropic.com"
        assert body["messages"][-1]["content"][0]["content"] == content
    actual, _ = authorize_agent_sdk_kwargs(agent, body)
    assert actual == body


@pytest.mark.parametrize("name", ["shell", ""])
@pytest.mark.parametrize(
    "mutation",
    [
        "suffix",
        "mismatch",
        "duplicate_call",
        "duplicate_result",
        "reorder",
        "label",
        "user_text",
        "cross_transport_duplicate",
        "cross_transport_call",
        "intervening_message",
        "secret",
        "path",
    ],
)
def test_native_correction_cannot_borrow_untrusted_result(
    tmp_path, monkeypatch, name, mutation
):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent = _agent(tmp_path)
    agent.provider = "anthropic"
    agent.api_mode = "anthropic_messages"
    agent.base_url = "https://api.anthropic.com"
    agent.valid_tool_names = {"read_file", "terminal"}
    body = native_frame(
        name, _invalid_tool_name_error_content(name, agent.valid_tool_names)
    )
    messages = body["messages"]
    assistant = next(m for m in messages if m["role"] == "assistant")
    user = messages[-1]
    result = user["content"][0]
    call = next(b for b in assistant["content"] if b["type"] == "tool_use")

    def append(text):
        result["content"] += text

    actions = {
        "suffix": lambda: append(" SGVsbG8gd29ybGQ="),
        "mismatch": lambda: result.update(tool_use_id="call_other"),
        "duplicate_call": lambda: assistant["content"].append(copy.deepcopy(call)),
        "duplicate_result": lambda: user["content"].append(copy.deepcopy(result)),
        "reorder": lambda: messages.insert(0, messages.pop()),
        "label": lambda: result.update(name="other"),
        "user_text": lambda: result.update(
            type="text", text=result.pop("content") + " SGVsbG8gd29ybGQ="
        ),
        "cross_transport_duplicate": lambda: assistant.update(
            tool_calls=frame(name, "")["messages"][0]["tool_calls"]
        ),
        "cross_transport_call": lambda: (
            assistant.update(tool_calls=frame(name, "")["messages"][0]["tool_calls"]),
            assistant["content"].remove(call),
        ),
        "intervening_message": lambda: messages.insert(
            len(messages) - 1, {"role": "user", "content": "unrelated turn"}
        ),
        "secret": lambda: append(" sk-proj-" + "x" * 48),
        "path": lambda: append(" /Users/private/credential.txt"),
    }
    actions[mutation]()
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, body)


@pytest.mark.parametrize("name", ["SGVsbG8gd29ybGQ=", "SGVsbG8gd29ybGQ"])
def test_native_diagnostic_keeps_attacker_name_encoding_scan(
    tmp_path, monkeypatch, name
):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent = _agent(tmp_path)
    agent.provider = "anthropic"
    agent.api_mode = "anthropic_messages"
    agent.base_url = "https://api.anthropic.com"
    agent.valid_tool_names = {"read_file", "terminal"}
    body = native_frame(
        name, _invalid_tool_name_error_content(name, agent.valid_tool_names)
    )
    with pytest.raises(EgressBlocked):
        authorize_agent_sdk_kwargs(agent, body)


@pytest.mark.parametrize("transport", ["openai", "anthropic"])
def test_same_batch_blank_and_unknown_diagnostics_keep_each_result(
    tmp_path, monkeypatch, transport
):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent = _agent(tmp_path)
    agent.valid_tool_names = {"read_file", "terminal"}
    first = frame("", _invalid_tool_name_error_content("", agent.valid_tool_names))
    second = frame(
        "shell",
        _invalid_tool_name_error_content("shell", agent.valid_tool_names),
        "call_two",
    )
    first["messages"][0]["tool_calls"].extend(second["messages"][0]["tool_calls"])
    first["messages"].append(second["messages"][-1])
    if transport == "anthropic":
        _, messages = convert_messages_to_anthropic(first["messages"])
        first["messages"] = messages
        agent.provider = "anthropic"
        agent.api_mode = "anthropic_messages"
        agent.base_url = "https://api.anthropic.com"
    actual, _ = authorize_agent_sdk_kwargs(agent, first)
    assert actual == first
