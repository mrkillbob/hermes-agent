"""Tests for codex source-provenance sidecar and egress-authorization flow.

Regression for codex egress provenance (see agent/source_provenance_tools.py and
agent/llm_egress_runtime.py). Kept in a separate file because _build_agent here
sets _llm_egress_state_dir, which adds meaningful per-test overhead; isolating
avoids inflating the larger codex-responses suite.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import types
from types import SimpleNamespace

import pytest


sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

import run_agent


@pytest.fixture(autouse=True)
def _no_codex_backoff(monkeypatch):
    import time as _time
    monkeypatch.setattr("agent.retry_utils.jittered_backoff", lambda *a, **k: 0.0)
    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)


def _patch_agent_bootstrap(monkeypatch):
    monkeypatch.setattr(
        "model_tools.get_tool_definitions",
        lambda **kwargs: [
            {
                "type": "function",
                "function": {
                    "name": "terminal",
                    "description": "Run shell commands.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda: {})


def _build_agent(monkeypatch):
    _patch_agent_bootstrap(monkeypatch)

    agent = run_agent.AIAgent(
        model="gpt-5-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        api_key="codex-token",
        quiet_mode=True,
        max_iterations=4,
        skip_context_files=True,
        skip_memory=True,
    )
    agent._cleanup_task_resources = lambda task_id: None
    agent._persist_session = lambda messages, history=None: None
    agent._save_trajectory = lambda messages, user_message, completed: None
    return agent


class _FakeCreateStream:
    def __init__(self, events):
        self._events = list(events)
        self.closed = False

    def __iter__(self):
        return iter(self._events)

    def close(self):
        self.closed = True


def _codex_request_kwargs():
    return {
        "model": "gpt-5-codex",
        "instructions": "You are Hermes.",
        "input": [{"role": "user", "content": "Ping"}],
        "tools": None,
        "store": False,
    }


def test_build_api_kwargs_codex_carries_trusted_read_sidecar_to_authorization(
    tmp_path, monkeypatch
):
    """The live builder preserves trusted read provenance until authorization."""

    from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
    from agent.source_provenance_tools import (
        attach_trusted_source_provenance_metadata,
        source_provenance_activation,
    )
    from agent.tool_dispatch_helpers import make_tool_result_message
    from tools.file_tools import read_file_tool

    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    agent = _build_agent(monkeypatch)
    agent.session_id = "session-builder-replay"
    agent._current_turn_id = "turn-builder-replay"
    agent._current_api_request_id = "turn-builder-replay:api:1"
    agent._llm_egress_policy_digest = sha256(b"builder-replay-policy").hexdigest()
    agent._llm_egress_state_dir = tmp_path / "egress"

    with tempfile.TemporaryDirectory(
        prefix="hermes-builder-replay-", dir=Path.cwd()
    ) as source_root:
        source = Path(source_root) / "source.py"
        source.write_text("safe = True\n", encoding="utf-8")
        source_argument = str(source.relative_to(Path.cwd()))

        with source_provenance_activation(agent, "read_file"):
            result = read_file_tool(
                source_argument, task_id="codex-builder-replay-read"
            )
        metadata = attach_trusted_source_provenance_metadata(
            agent, "read_file", content=result
        )
        tool_result = make_tool_result_message(
            "read_file",
            result,
            "call_read_file_builder_replay",
            source_provenance=metadata,
        )
        api_messages = [
            {"role": "system", "content": "You are Hermes."},
            {"role": "user", "content": "Inspect the granted source."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_read_file_builder_replay",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": source_argument}),
                        },
                    }
                ],
            },
            tool_result,
        ]

        built = agent._build_api_kwargs(api_messages, tools_for_api=[])

        sidecar = built["_hermes_source_provenance"]
        assert len(sidecar) == 1
        assert sidecar[0]["tool_call_id"] == "call_read_file_builder_replay"
        assert sidecar[0]["content_sha256"] == sha256(
            result.encode("utf-8")
        ).hexdigest()
        assert result not in json.dumps(sidecar)
        converted_result = next(
            item
            for item in built["input"]
            if item.get("type") == "function_call_output"
        )
        assert converted_result["output"] == result
        assert "_source_provenance" not in converted_result
        assert "_hermes_source_provenance" not in converted_result

        agent._current_api_request_id = "turn-builder-replay:api:2"
        authorized, receipt = authorize_agent_sdk_kwargs(agent, built)

        authorized_result = next(
            item
            for item in authorized["input"]
            if item.get("type") == "function_call_output"
        )
        assert authorized_result["output"] == result
        assert "_source_provenance" not in json.dumps(authorized)
        assert "_hermes_source_provenance" not in authorized
        assert receipt.decision.source_grant_count == 1
        assert receipt.decision.source_segment_count == 1


def test_run_codex_stream_keeps_provenance_sidecar_internal(monkeypatch, tmp_path):
    agent = _build_agent(monkeypatch)
    agent.session_id = "session-1"
    agent._current_turn_id = "turn-1"
    agent._current_api_request_id = "turn-1:api:2"
    agent._llm_egress_policy_digest = "a" * 64
    agent._llm_egress_state_dir = tmp_path / "egress"
    captured = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeCreateStream([
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(status="completed"),
            )
        ])

    agent.client = SimpleNamespace(
        responses=SimpleNamespace(create=_fake_create),
    )
    request = {
        **_codex_request_kwargs(),
        "_hermes_source_provenance": [],
    }

    agent._run_codex_stream(request)

    assert "_hermes_source_provenance" not in captured


def test_codex_preflight_preserves_internal_provenance_sidecar():
    from agent.transports.codex import ResponsesApiTransport

    sidecar = [{"tool_call_id": "call_read_1", "content_sha256": "a" * 64}]
    request = {
        **_codex_request_kwargs(),
        "_hermes_source_provenance": sidecar,
    }

    normalized = ResponsesApiTransport().preflight_kwargs(request)

    assert normalized["_hermes_source_provenance"] is sidecar
