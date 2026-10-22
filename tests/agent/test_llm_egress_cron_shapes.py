"""Real protected inventory shapes retain count-only, exact-call provenance."""

import copy
from hashlib import sha256
import json

import pytest

from agent.llm_egress_cron import project_cron_list
from agent.llm_egress_firewall import (
    EgressBlocked,
    GeneratedContextSegment,
    OutboundText,
    SanitizedSegment,
    UntrustedProvenanceSegment,
)
from tests.agent.test_llm_egress_cron_list_projection import dispatch, request


def with_arguments(body, extra):
    function = body["messages"][0]["tool_calls"][0]["function"]
    args = json.loads(function["arguments"])
    args["calls"][0]["arguments"].update(extra)
    function["arguments"] = json.dumps(args)
    return body


def test_encoded_private_inventory_uses_only_count(tmp_path, monkeypatch):
    body = request(
        result={
            "success": True,
            "count": 1,
            "jobs": [
                {"prompt": "c2VjcmV0LXBheWxvYWQ=", "workdir": "/Users/private/operator"}
            ],
            "gateway_running": True,
        }
    )
    original = copy.deepcopy(body)
    sent = dispatch(tmp_path, monkeypatch, body)
    assert json.loads(sent["messages"][1]["content"]) == {
        "success": True,
        "count": 1,
        "jobs_omitted": True,
    }
    assert body == original
    assert "c2VjcmV0" not in repr(sent)
    assert "/Users/private" not in repr(sent)


def test_legacy_ignored_list_arguments_reach_count_only(tmp_path, monkeypatch):
    body = with_arguments(request(), {"limit": 5, "job": "weekly-audit"})
    sent = dispatch(tmp_path, monkeypatch, body)
    assert json.loads(sent["messages"][1]["content"]) == {
        "success": True,
        "count": 0,
        "jobs": [],
    }
    assert sent["messages"][0] == body["messages"][0]


def test_real_handler_ignores_legacy_list_arguments(monkeypatch):
    from tools import cronjob_tools

    observed = []
    monkeypatch.setattr(
        cronjob_tools, "list_jobs", lambda **kw: observed.append(kw) or []
    )
    result = cronjob_tools._cronjob_handler(
        {"action": "list", "limit": 5, "job": "weekly-audit"}
    )
    assert json.loads(result) == {"success": True, "count": 0, "jobs": []}
    assert observed == [{"include_disabled": True}]


@pytest.mark.parametrize(
    "extra",
    [
        {"limit": True},
        {"limit": -1},
        {"limit": "5"},
        {"job": []},
        {"job": "c2VjcmV0LXBheWxvYWQ="},
        {"job": "c2VjcmV0cGF5bG9hZA"},
        {"job": "ghp_abcdefghijklmnopqrstuvwxyz1234567890ABCD"},
        {"job": "/Users/private/operator"},
        {"job_id": "weekly-audit"},
        {"unknown": "value"},
    ],
)
def test_legacy_arguments_do_not_admit_other_shapes(tmp_path, monkeypatch, extra):
    body = with_arguments(request(), extra)
    with pytest.raises(EgressBlocked):
        dispatch(tmp_path, monkeypatch, body)


@pytest.mark.parametrize("mutation", ["digest", "text", "extra", "order", "type"])
def test_untrusted_wrappers_require_exact_bound_composition(mutation):
    body = request(
        result={
            "success": True,
            "count": 1,
            "jobs": [{"prompt": "c2VjcmV0LXBheWxvYWQ="}],
        }
    )
    raw = body["messages"][1]["content"]
    marker = UntrustedProvenanceSegment(sha256(raw.encode()).hexdigest())
    variants = {
        "digest": [UntrustedProvenanceSegment("0" * 64), SanitizedSegment(raw)],
        "text": [marker, SanitizedSegment(raw + "unrelated")],
        "extra": [marker, SanitizedSegment(raw), SanitizedSegment("unrelated")],
        "order": [SanitizedSegment(raw), marker],
        "type": [marker, GeneratedContextSegment(raw)],
    }
    parts = variants[mutation]
    typed = {
        **body,
        "messages": [
            body["messages"][0],
            {**body["messages"][1], "content": OutboundText(tuple(parts))},
        ],
    }
    assert project_cron_list(body, typed, True) == typed


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_call",
        "duplicate_result",
        "wrong_id",
        "wrong_label",
        "late_call",
        "mutation",
        "extra_result",
        "count_mismatch",
        "disabled",
    ],
)
def test_diagnostic_wrapper_cannot_bypass_binding_or_schema(
    tmp_path, monkeypatch, case
):
    from agent.llm_egress_runtime import dispatch_authorized_agent_request
    from tests.agent.test_llm_egress_runtime import _agent

    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    body = request(
        result={
            "success": True,
            "count": 1,
            "jobs": [{"prompt": "c2VjcmV0LXBheWxvYWQ="}],
        }
    )
    call = body["messages"][0]["tool_calls"][0]
    output = body["messages"][1]
    args = json.loads(call["function"]["arguments"])
    result = json.loads(output["content"])
    mutations = {
        "duplicate_call": lambda: body["messages"][0]["tool_calls"].append(
            copy.deepcopy(call)
        ),
        "duplicate_result": lambda: body["messages"].append(copy.deepcopy(output)),
        "wrong_id": lambda: output.update(tool_call_id="other_call"),
        "wrong_label": lambda: output.update(tool_name="terminal"),
        "late_call": lambda: body["messages"].reverse(),
        "mutation": lambda: args["calls"][0]["arguments"].update(action="resume"),
        "extra_result": lambda: result.update(raw="unapproved"),
        "count_mismatch": lambda: result.update(count=2),
        "disabled": lambda: monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE"),
    }
    mutations[case]()
    call["function"]["arguments"] = json.dumps(args)
    output["content"] = json.dumps(result)
    with pytest.raises(EgressBlocked):
        dispatch_authorized_agent_request(
            _agent(tmp_path), body, lambda _: pytest.fail("sent")
        )
