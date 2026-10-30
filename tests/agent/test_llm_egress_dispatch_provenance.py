"""Dispatch must honor exact trusted-read provenance before invoking the sender."""

import pytest

from agent.llm_egress_firewall import EgressBlocked
from tests.agent.test_llm_egress_runtime import _agent


def test_dispatch_allows_only_exact_provenance_bound_read_file_result(
    tmp_path, monkeypatch
):
    import agent.llm_egress_runtime as runtime
    from agent.llm_egress_runtime import dispatch_authorized_agent_request
    from agent.source_provenance_tools import (
        attach_trusted_source_provenance_metadata,
        source_provenance_activation,
    )
    from agent.tool_dispatch_helpers import make_tool_result_message
    from tools.file_tools import read_file_tool

    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    source = tmp_path / "source.py"
    source.write_text("publicly admitted source = True\n", encoding="utf-8")
    agent = _agent(tmp_path / "egress")
    agent._current_api_request_id = "turn-1:api:1"

    with source_provenance_activation(agent, "read_file"):
        result = read_file_tool(str(source), task_id="egress-real-read-dispatch")
    metadata = attach_trusted_source_provenance_metadata(
        agent, "read_file", content=result
    )
    message = make_tool_result_message(
        "read_file", result, "call_read_dispatch", source_provenance=metadata
    )
    agent._current_api_request_id = "turn-1:api:2"
    sent = []
    decisions = []
    authorize = runtime.authorize_agent_sdk_kwargs

    def capture_decision(*args, **kwargs):
        try:
            authorized, receipt = authorize(*args, **kwargs)
        except EgressBlocked as exc:
            decisions.append(exc.decision)
            raise
        decisions.append(receipt)
        return authorized, receipt

    monkeypatch.setattr(runtime, "authorize_agent_sdk_kwargs", capture_decision)

    dispatch_authorized_agent_request(
        agent,
        {"model": "test-model", "messages": [message]},
        lambda request: sent.append(request) or "sent",
    )

    assert len(sent) == 1
    assert sent[0]["messages"][0]["content"] == result
    assert "_source_provenance" not in sent[0]["messages"][0]
    assert decisions[0].allowed
    assert decisions[0].decision.source_grant_count == 1
    assert decisions[0].decision.source_segment_count == 1

    altered = dict(message, content=result + "tampered")
    with pytest.raises(EgressBlocked) as altered_block:
        dispatch_authorized_agent_request(
            agent,
            {"model": "test-model", "messages": [altered]},
            lambda request: sent.append(request) or "sent",
        )
    assert decisions[-1] is altered_block.value.decision
    assert not decisions[-1].allowed
    assert decisions[-1].source_grant_count == 0
    assert "untrusted_provenance" in decisions[-1].reason_codes
    assert len(sent) == 1

    unproven = dict(message)
    unproven.pop("_source_provenance")
    with pytest.raises(EgressBlocked) as unproven_block:
        dispatch_authorized_agent_request(
            agent,
            {"model": "test-model", "messages": [unproven]},
            lambda request: sent.append(request) or "sent",
        )
    assert decisions[-1] is unproven_block.value.decision
    assert not decisions[-1].allowed
    assert decisions[-1].source_grant_count == 0
    assert "untrusted_provenance" in decisions[-1].reason_codes
    assert len(sent) == 1
