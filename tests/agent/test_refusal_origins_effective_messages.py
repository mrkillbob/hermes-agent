"""Refusal diagnostics follow the SDK's effective messages (PR369 review)."""

from copy import deepcopy
import json

import pytest

from tests.agent.test_llm_egress_refusal_origins import (
    _ENCODED,
    _agent,
    _blocked_receipt,
)


def _call():
    return {"role": "assistant", "tool_calls": [{
        "id": "synthetic-call", "type": "function",
        "function": {"name": "synthetic_tool", "arguments": "{}"},
    }]}


def _result():
    return {"role": "tool", "tool_call_id": "synthetic-call", "content": _ENCODED}


@pytest.mark.parametrize(
    ("root", "effective", "origins"),
    [
        pytest.param([], [{"role": role, "content": _ENCODED}], [origin], id=role)
        for role, origin in (
            ("user", "user_task"), ("system", "system_or_schema"),
            ("developer", "system_or_schema"), ("assistant", "assistant_history"),
        )
    ] + [
        pytest.param([], [_call(), _result()], ["tool_result"], id="paired-tool"),
        pytest.param([_call()], [_result()], ["other"], id="no-shadowed-call-borrowing"),
        pytest.param([], [_result(), _call()], ["other"], id="result-before-call"),
        pytest.param([], [_call(), _call(), _result()], ["other"], id="duplicate-call"),
        pytest.param([], [_call(), _result(), _result()], ["other"], id="duplicate-result"),
        pytest.param([{"role": "system", "content": _ENCODED}],
                     [{"role": "user", "content": _ENCODED}], ["user_task"],
                     id="shadowed-root-does-not-contribute"),
        pytest.param([_call(), _result()], [], ["other"], id="empty-overrides-root"),
        pytest.param([_call(), _result()], None, ["other"], id="null-overrides-root"),
        pytest.param([_call(), _result()], {"role": "user", "content": _ENCODED},
                     ["other"], id="malformed-overrides-root"),
        pytest.param([], [{"role": "user", "content": {
            "messages": [_call(), _result()],
        }}], ["user_task"], id="nested-tool-spoof-stays-user"),
    ],
)
def test_effective_messages_keep_roles_and_pairing_without_shadowed_evidence(
    tmp_path, root, effective, origins
):
    request = {"model": "synthetic-model", "messages": root,
               "extra_body": {"messages": effective}}
    original = deepcopy(request)
    decision, receipt = _blocked_receipt(_agent(tmp_path), request)

    assert decision.allowed is False
    assert "base64_payload" in decision.reason_codes
    diagnostics = receipt["source_provenance_diagnostics"]
    assert diagnostics["refusal_origins"] == origins
    assert request == original
    serialized = json.dumps(diagnostics)
    assert _ENCODED not in serialized
    assert "synthetic-call" not in serialized


def test_real_sdk_bypass_preserves_explicit_effective_messages(monkeypatch):
    """Characterize the exact SDK precedence used by these diagnostic cases."""
    from agent.sdk_transform_bypass import ESCAPE_HATCH_ENV, bypass_chat_sdk_request_transform
    from tests.agent.test_chat_sdk_transform_bypass import _Recorder

    monkeypatch.delenv(ESCAPE_HATCH_ENV, raising=False)
    recorder = _Recorder()
    root = [{"role": "user", "content": "shadowed synthetic text"}]
    effective = [{"role": "user", "content": "effective synthetic text"}]
    request = {"model": "synthetic-model", "messages": root,
               "extra_body": {"messages": effective}, "stream": True}
    try:
        moved = bypass_chat_sdk_request_transform(request, recorder.client)
        assert json.loads(recorder.send(moved))["messages"] == effective
        assert request["messages"] == root
    finally:
        recorder.client.close()
