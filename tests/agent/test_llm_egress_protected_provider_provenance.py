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
    "provider,protected_flag,transport,shape",
    [
        (provider, flag, transport, shape)
        for provider in ["openai-codex", "nous", "nous-portal", "nousresearch", "anthropic"]
        for flag in [None, "0", "1"]
        for transport in ["chat", "responses"]
        for shape in ["text", "text_block", "mapping"]
    ] + [
        ("anthropic", flag, "anthropic", shape)
        for flag in [None, "0"]
        for shape in ["text", "text_block", "mapping"]
    ],
)
def test_protected_provider_denies_raw_output_or_uses_bounded_worker_projection(
    tmp_path, monkeypatch, provider, protected_flag, transport, shape
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
        field, content_key = "messages", "content"
        request = {field: [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": call_id, "name": "terminal", "input": {}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call_id, "content": output}
            ]},
        ]}
    else:
        field, content_key = "input", "output"
        request = {field: [
            {"id": call_id, "call_id": call_id, "type": "function", "function": function},
            {"type": "function_call_output", "call_id": call_id, content_key: output},
        ]}
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
    ],
)
def test_existing_marked_custom_worker_admission_is_preserved(
    tmp_path, monkeypatch, surface, output
):
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
