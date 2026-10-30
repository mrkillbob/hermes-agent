"""Protected cloud routes require terminal provenance without Kanban markers."""

from __future__ import annotations

import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.chat_completion_helpers import _dispatch_provider_request
from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs


def _agent(tmp_path, *, provider="nous", api_mode="chat_completions"):
    if provider == "openai-codex":
        base_url = "https://chatgpt.com/backend-api/codex"
    elif provider == "anthropic":
        base_url = "https://api.anthropic.com/v1"
    else:
        base_url = "https://inference-api.nousresearch.com/v1"
    return SimpleNamespace(
        provider=provider,
        model="test-model",
        base_url=base_url,
        api_mode=api_mode,
        session_id="session-1",
        _current_turn_id="turn-1",
        _current_api_request_id="request-1",
        _llm_egress_policy_digest=sha256(b"policy").hexdigest(),
        _llm_egress_state_dir=tmp_path,
    )


@pytest.mark.parametrize(
    "provider,protected_flag,transport,shape,binding",
    [
        (provider, flag, transport, shape, "matching")
        for provider in ["openai-codex", "nous", "nous-portal", "nousresearch", "anthropic"]
        for flag in [None, "0", "1"]
        for transport in ["chat", "responses"]
        for shape in ["text", "text_block", "mapping"]
    ] + [
        (provider, flag, transport, shape, "missing")
        for provider in ["openai-codex", "nous", "nous-portal", "nousresearch", "anthropic"]
        for flag in [None, "0"]
        for transport in ["chat", "responses"]
        for shape in ["text", "text_block", "mapping"]
    ] + [
        ("anthropic", flag, "anthropic", shape, binding)
        for flag in [None, "0"]
        for shape in ["text", "text_block", "mapping"]
        for binding in ["matching", "mismatched", "missing"]
    ],
)
def test_protected_provider_denies_raw_output_or_uses_bounded_worker_projection(
    tmp_path, monkeypatch, provider, protected_flag, transport, shape, binding
):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    if protected_flag is None:
        monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
    else:
        monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", protected_flag)
    raw = "def calculate_total(items):\n    return sum(items)\n"
    block_type = "input_text" if transport == "responses" else "text"
    if shape == "text":
        output = raw
    elif shape == "text_block":
        output = [{"type": block_type, "text": raw}]
    else:
        output = {"text": raw}
    call_id = "call_terminal123"
    function = {"name": "terminal", "arguments": "{}"}
    if transport == "chat":
        field, content_key = "messages", "content"
        request = {field: [
            {"role": "assistant", "tool_calls": [
                {"id": call_id, "type": "function", "function": function}
            ]},
            {"role": "tool", "tool_call_id": call_id, content_key: output},
        ]}
    elif transport == "anthropic":
        native_result = {"type": "tool_result", "content": output}
        if binding != "missing":
            native_result["tool_use_id"] = call_id if binding == "matching" else "unbound"
        field, content_key = "messages", "content"
        request = {field: [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": call_id, "name": "terminal", "input": {}}
            ]},
            {"role": "user", "content": [
                native_result
            ]},
        ]}
    else:
        field, content_key = "input", "output"
        request = {field: [
            {"id": call_id, "call_id": call_id, "type": "function", "function": function},
            {"type": "function_call_output", "call_id": call_id, content_key: output},
        ]}
    if binding == "missing" and transport in {"chat", "responses"}:
        request[field][1].pop("tool_call_id" if transport == "chat" else "call_id")
    callback = MagicMock(return_value="allowed")
    if protected_flag == "1":
        assert _dispatch_provider_request(
            _agent(tmp_path, provider=provider), request, callback
        ) == "allowed"
        projected = callback.call_args.args[0][field][1][content_key]
        if isinstance(projected, list):
            projected = projected[0]["text"]
        assert json.loads(projected) == {
            "terminal_result": "completed",
            "exit_code": None,
            "raw_output": "omitted_from_remote_replay",
        }
        assert raw not in projected
    else:
        with pytest.raises(EgressBlocked) as exc_info:
            _dispatch_provider_request(
                _agent(tmp_path, provider=provider), request, callback
            )
        assert "untrusted_provenance" in exc_info.value.decision.reason_codes
        callback.assert_not_called()

    callback.reset_mock()
    local = _agent(tmp_path, provider="ollama-launch")
    local.base_url = "http://127.0.0.1:11434/v1"
    callback.return_value = "local"
    assert _dispatch_provider_request(local, request, callback) == "local"
    callback.assert_called_once_with(request)


@pytest.mark.parametrize(
    "surface,output",
    [
        ("content", "def calculate_total(items):\n    return sum(items)\n"),
        ("content", "PASS _SCHWAB_PARENT_SEED_ASSEMBLER line 5243"),
        ("output", "https://github.com/acme/widget.git\nworking tree clean"),
    ] + [
        (f"native:{name}:{binding}", output)
        for name in ["read_file", "mcp__read_file", "memory", "mcp__context_notes",
                     "tool_describe", "mcp__tool_describe", "tool_search", "mcp__tool_search",
                     "tool_call", "mcp__tool_call", "unknown", "mcp__terminal"]
        for binding in ["matching", "missing", "mismatched", "duplicate", "future",
                        "orphan_then_matching", "repeated_result", "assistant_result",
                        "list_id", "mapping_id", "zero_id",
                        "conflicting_tool_call_id", "conflicting_call_id"]
        for output in ["bounded local status: complete", "token=synthetic-secret-value"]
    ],
)
def test_native_nonterminal_binding_and_existing_worker_admission_are_preserved(
    tmp_path, monkeypatch, surface, output
):
    if surface.startswith("native:"):
        from tools.registry import registry as tool_registry

        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
        monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
        # The callback test isolates identity binding; E2E uses real registered
        # tools, source capture and the actual native transport.
        monkeypatch.setattr(tool_registry, "get_entry", lambda name: (
            object() if name in {"read_file", "memory", "terminal"} else None
        ))
        _, name, binding = surface.split(":")
        call = {"type": "tool_use", "id": "call_native", "name": name, "input": {}}
        result = {"type": "tool_result", "content": output}
        if binding != "missing":
            result["tool_use_id"] = {
                "mismatched": "other", "list_id": ["call_native"],
                "mapping_id": {"id": "call_native"}, "zero_id": 0,
            }.get(binding, "call_native")
        calls = [call, dict(call)] if binding == "duplicate" else [call]
        messages = [
            {"role": "assistant", "content": calls},
            {"role": "user", "content": [result]},
        ]
        if binding == "future":
            messages.reverse()
        elif binding == "orphan_then_matching":
            messages.insert(0, {"role": "user", "content": [dict(result)]})
        elif binding == "repeated_result":
            messages.append({"role": "user", "content": [dict(result)]})
        elif binding == "assistant_result":
            messages.insert(0, {"role": "assistant", "content": [dict(result)]})
        elif binding in {"conflicting_tool_call_id", "conflicting_call_id"}:
            messages[1]["content"] = [{
                "type": "tool_result", "tool_use_id": "call_native",
                "content": "bounded local status: complete",
            }]
            result["tool_use_id"] = "unknown"
            result[binding.removeprefix("conflicting_")] = "call_native"
            messages.append({"role": "user", "content": [result]})
        request = {"messages": messages}
        callback = MagicMock(return_value="allowed")
        allowed = (binding == "matching" and name not in {"tool_call", "mcp__tool_call", "unknown", "mcp__terminal"}
                   and not output.startswith("token="))
        if allowed:
            assert _dispatch_provider_request(_agent(tmp_path, provider="anthropic"), request, callback) == "allowed"
            callback.assert_called_once()
        else:
            with pytest.raises(EgressBlocked):
                _dispatch_provider_request(_agent(tmp_path, provider="anthropic"), request, callback)
            callback.assert_not_called()
        return
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent = _agent(tmp_path, provider="custom")
    agent.base_url = "https://llm.example.test/v1"
    call_id = "call_terminal123"
    function = {"name": "terminal", "arguments": "{}"}
    if surface == "content":
        request = {"messages": [
            {"role": "assistant", "tool_calls": [
                {"id": call_id, "type": "function", "function": function}
            ]},
            {"role": "tool", "tool_call_id": call_id, "content": output},
        ]}
        field = "messages"
    else:
        request = {"input": [
            {"id": call_id, "call_id": call_id, "type": "function", "function": function},
            {"type": "function_call_output", "call_id": call_id, "output": output},
        ]}
        field = "input"
    authorized, receipt = authorize_agent_sdk_kwargs(agent, request)
    assert receipt.allowed
    assert authorized[field][1][surface] == output
    assert receipt.decision.source_segment_count == 0


_NATIVE_PARITY_TOOL = "mcp__editor_a2a_native_fixture__a2a_submit"
_NATIVE_PARITY_SCOPE = "session:egress-native-protocol-parity"


def _native_protocol_frames(protocol, output, *, call_id="call_native_parity", name=_NATIVE_PARITY_TOOL):
    call = {
        "chat": {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}},
        "anthropic": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
        "responses": {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"},
    }[protocol]
    result = {
        "chat": {"role": "tool", "tool_call_id": call_id, "content": output},
        "anthropic": {"type": "tool_result", "tool_use_id": call_id, "content": output},
        "responses": {"type": "function_call_output", "call_id": call_id, "output": output},
    }[protocol]
    if protocol == "chat":
        body = {"messages": [{"role": "assistant", "tool_calls": [call]}, result]}
    elif protocol == "anthropic":
        body = {"messages": [
            {"role": "assistant", "content": [call]},
            {"role": "user", "content": [result]},
        ]}
    else:
        body = {"input": [call, result]}
    return body, call, result


def _mutate_native_protocol_body(body, call, result, protocol, invalid_case):
    def duplicate():
        {
            "chat": lambda: body["messages"][0]["tool_calls"].append(dict(call)),
            "anthropic": lambda: body["messages"][0]["content"].append(dict(call)),
            "responses": lambda: body["input"].insert(1, dict(call)),
        }[protocol]()

    def reverse_frames():
        body[{"chat": "messages", "anthropic": "messages", "responses": "input"}[protocol]].reverse()

    def move_call_to_non_assistant():
        {
            "chat": lambda: body["messages"][0].update(role="user"),
            "anthropic": lambda: body["messages"][0].update(role="user"),
            "responses": lambda: body["input"].__setitem__(
                0, {"type": "message", "role": "user", "content": [call]}
            ),
        }[protocol]()

    def nest_call_and_result():
        body.clear()
        body["metadata"] = {"nested": [call, result]}

    def copy_result_into_nested_data():
        body["metadata"] = {"nested": dict(result)}

    {
        "unregistered": lambda: {
            "chat": lambda: call["function"].update(
                name="mcp__editor_a2a_native_fixture__not_registered"
            ),
            "anthropic": lambda: call.update(
                name="mcp__editor_a2a_native_fixture__not_registered"
            ),
            "responses": lambda: call.update(
                name="mcp__editor_a2a_native_fixture__not_registered"
            ),
        }[protocol](),
        "duplicate": duplicate,
        "future": reverse_frames,
        "mismatch": lambda: result.update(
            **{
                {"chat": "tool_call_id", "anthropic": "tool_use_id", "responses": "call_id"}[protocol]: "other"
            }
        ),
        "non_assistant": move_call_to_non_assistant,
        "nested": nest_call_and_result,
        "nested_result": copy_result_into_nested_data,
    }[invalid_case]()


@pytest.fixture
def _native_provider_dispatch_context():
    """Register a synthetic tool only in a context-local test overlay."""
    from tools.registry import registry

    registry.deregister(_NATIVE_PARITY_TOOL, scope=_NATIVE_PARITY_SCOPE)
    registry.register(
        _NATIVE_PARITY_TOOL,
        "session-fixture",
        {"name": _NATIVE_PARITY_TOOL, "parameters": {"type": "object"}},
        lambda _args: None,
        scope=_NATIVE_PARITY_SCOPE,
    )
    try:
        yield
    finally:
        registry.deregister(_NATIVE_PARITY_TOOL, scope=_NATIVE_PARITY_SCOPE)


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_registered_session_native_results_are_admitted_by_explicit_protocol(
    tmp_path, monkeypatch, protocol, _native_provider_dispatch_context
):
    from tools.registry import registry, session_tool_scope

    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
    body, _, _ = _native_protocol_frames(protocol, "bounded synthetic output")
    callback = MagicMock(return_value="allowed")
    provider = "anthropic" if protocol == "anthropic" else "nous"
    api_mode = "responses" if protocol == "responses" else ("anthropic" if protocol == "anthropic" else "chat_completions")
    with session_tool_scope(_NATIVE_PARITY_SCOPE):
        assert registry.get_entry(_NATIVE_PARITY_TOOL) is not None
        assert _dispatch_provider_request(
            _agent(tmp_path, provider=provider, api_mode=api_mode), body, callback
        ) == "allowed"
        callback.assert_called_once()

        for rejected in ("token=synthetic-secret-value", "c2VjcmV0LXBheWxvYWQ="):
            unsafe, _, _ = _native_protocol_frames(protocol, rejected)
            callback.reset_mock()
            with pytest.raises(EgressBlocked):
                _dispatch_provider_request(
                    _agent(tmp_path, provider=provider, api_mode=api_mode), unsafe, callback
                )
            callback.assert_not_called()

    with session_tool_scope("session:egress-native-protocol-other"):
        assert registry.get_entry(_NATIVE_PARITY_TOOL) is None
        callback.reset_mock()
        with pytest.raises(EgressBlocked):
            _dispatch_provider_request(
                _agent(tmp_path, provider=provider, api_mode=api_mode), body, callback
            )
        callback.assert_not_called()

    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    callback.reset_mock()
    with session_tool_scope(_NATIVE_PARITY_SCOPE), pytest.raises(EgressBlocked):
        _dispatch_provider_request(
            _agent(tmp_path, provider=provider, api_mode=api_mode), body, callback
        )
    callback.assert_not_called()


def test_registered_session_responses_frames_are_admitted_after_sdk_bypass(
    tmp_path, monkeypatch, _native_provider_dispatch_context
):
    """The exact Responses wire input moved to extra_body keeps its call binding."""
    from tools.registry import session_tool_scope

    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
    body, _, _ = _native_protocol_frames("responses", "bounded synthetic output")
    request = {"extra_body": {"input": body.pop("input")}}
    callback = MagicMock(return_value="allowed")
    with session_tool_scope(_NATIVE_PARITY_SCOPE):
        assert _dispatch_provider_request(
            _agent(tmp_path, provider="nous", api_mode="responses"), request, callback
        ) == "allowed"
    callback.assert_called_once()

    for rejected in ("token=synthetic-secret-value", "c2VjcmV0LXBheWxvYWQ="):
        unsafe, _, _ = _native_protocol_frames("responses", rejected)
        callback.reset_mock()
        with session_tool_scope(_NATIVE_PARITY_SCOPE), pytest.raises(EgressBlocked):
            _dispatch_provider_request(
                _agent(tmp_path, provider="nous", api_mode="responses"),
                {"extra_body": {"input": unsafe.pop("input")}},
                callback,
            )
        callback.assert_not_called()


def test_responses_extra_body_override_cannot_borrow_top_level_call_id(
    tmp_path, monkeypatch, _native_provider_dispatch_context
):
    """Only the effective SDK-merged input may bind a direct native result."""
    from tools.registry import session_tool_scope

    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
    top_level, _, result = _native_protocol_frames(
        "responses", "bounded synthetic output"
    )
    # The SDK merges extra_body after the typed JSON, so this output replaces
    # the valid top-level call/output pair. Reusing the same object also checks
    # that identity-based result admission cannot borrow across the collision.
    request = {
        "input": top_level["input"],
        "extra_body": {"input": [result]},
    }
    callback = MagicMock(return_value="allowed")
    with session_tool_scope(_NATIVE_PARITY_SCOPE), pytest.raises(EgressBlocked):
        _dispatch_provider_request(
            _agent(tmp_path, provider="nous", api_mode="responses"), request, callback
        )
    callback.assert_not_called()


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize(
    "invalid_case",
    ["unregistered", "duplicate", "future", "mismatch", "non_assistant", "nested", "nested_result"],
)
def test_native_protocol_admission_rejects_unbound_or_nested_frames(
    tmp_path, monkeypatch, protocol, invalid_case, _native_provider_dispatch_context
):
    from tools.registry import session_tool_scope

    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
    body, call, result = _native_protocol_frames(protocol, "bounded synthetic output")
    _mutate_native_protocol_body(body, call, result, protocol, invalid_case)

    provider = "anthropic" if protocol == "anthropic" else "nous"
    api_mode = "responses" if protocol == "responses" else ("anthropic" if protocol == "anthropic" else "chat_completions")
    callback = MagicMock(return_value="allowed")
    with session_tool_scope(_NATIVE_PARITY_SCOPE), pytest.raises(EgressBlocked):
        _dispatch_provider_request(
            _agent(tmp_path, provider=provider, api_mode=api_mode), body, callback
        )
    callback.assert_not_called()
