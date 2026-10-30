"""Protected cron inventory replays counts, never raw job metadata or mutations."""

import copy
import json

import pytest

from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import dispatch_authorized_agent_request
from tests.agent.test_llm_egress_runtime import _agent


def request(*, direct=False, result=None):
    arguments = {"action": "list"}
    function = {"name": "cronjob_manage", "arguments": json.dumps(arguments)}
    if not direct:
        function = {
            "name": "tool_call",
            "arguments": json.dumps(
                {"calls": [{"name": "cronjob_manage", "arguments": arguments}]}
            ),
        }
    return {
        "model": "test-model",
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "call_inventory", "type": "function", "function": function}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_inventory",
                "content": json.dumps(
                    result or {"success": True, "count": 0, "jobs": []}
                ),
            },
        ],
    }


def dispatch(tmp_path, monkeypatch, body):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    sent = []
    dispatch_authorized_agent_request(
        _agent(tmp_path), body, lambda value: sent.append(value)
    )
    assert len(sent) == 1
    return sent[0]


@pytest.mark.parametrize("direct", [False, True])
def test_exact_cron_list_reaches_sender_with_empty_inventory(
    tmp_path, monkeypatch, direct
):
    body = request(direct=direct)
    original = copy.deepcopy(body)
    sent = dispatch(tmp_path, monkeypatch, body)
    assert json.loads(sent["messages"][1]["content"]) == {
        "success": True,
        "count": 0,
        "jobs": [],
    }
    assert body == original


def test_nonempty_inventory_omits_all_private_job_fields(tmp_path, monkeypatch):
    body = request(
        result={
            "success": True,
            "count": 1,
            "jobs": [
                {
                    "prompt": "SECRET_JOB_PROMPT",
                    "workdir": "/Users/private/project",
                    "token": "SECRET_VALUE",
                }
            ],
        }
    )
    sent = dispatch(tmp_path, monkeypatch, body)
    assert json.loads(sent["messages"][1]["content"]) == {
        "success": True,
        "count": 1,
        "jobs_omitted": True,
    }
    assert "SECRET_" not in repr(sent)
    assert "/Users/private" not in repr(sent)


@pytest.mark.parametrize(
    "case",
    [
        "no_call",
        "late_call",
        "duplicate_call",
        "duplicate_result",
        "wrong_id",
        "wrong_label",
        "mutation",
        "multi_call",
        "extra_arguments",
        "unknown_tool",
        "extra_result",
        "failure",
        "count_bool",
        "count_mismatch",
        "not_jobs",
        "not_job_mapping",
    ],
)
def test_unsupported_or_ambiguous_results_never_reach_sender(
    tmp_path, monkeypatch, case
):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    body = request()
    call = body["messages"][0]["tool_calls"][0]
    output = body["messages"][1]
    args = json.loads(call["function"]["arguments"])
    result = json.loads(output["content"])
    mutations = {
        "no_call": lambda: body["messages"].pop(0),
        "late_call": lambda: body["messages"].reverse(),
        "duplicate_call": lambda: body["messages"][0]["tool_calls"].append(
            copy.deepcopy(call)
        ),
        "duplicate_result": lambda: body["messages"].append(copy.deepcopy(output)),
        "wrong_id": lambda: output.update(tool_call_id="call_other"),
        "wrong_label": lambda: output.update(name="terminal"),
        "mutation": lambda: args["calls"][0]["arguments"].update(action="resume"),
        "multi_call": lambda: args["calls"].append(copy.deepcopy(args["calls"][0])),
        "extra_arguments": lambda: args["calls"][0]["arguments"].update(
            job_id="unapproved"
        ),
        "unknown_tool": lambda: args["calls"][0].update(name="terminal"),
        "extra_result": lambda: result.update(raw="unapproved metadata"),
        "failure": lambda: result.update(success=False),
        "count_bool": lambda: result.update(count=False),
        "count_mismatch": lambda: result.update(count=1),
        "not_jobs": lambda: result.update(jobs="raw metadata"),
        "not_job_mapping": lambda: result.update(count=1, jobs=["raw metadata"]),
    }
    mutations[case]()
    call["function"]["arguments"] = json.dumps(args)
    output["content"] = json.dumps(result)
    sent = []
    with pytest.raises(EgressBlocked):
        dispatch_authorized_agent_request(
            _agent(tmp_path), body, lambda value: sent.append(value)
        )
    assert not sent


@pytest.mark.parametrize(
    "notice",
    [
        {"gateway_running": True},
        {"gateway_running": None},
        {
            "gateway_running": False,
            "warning": "Private host /Users/private/operator is offline",
        },
    ],
)
def test_real_nonempty_inventory_liveness_fields_are_validated_and_omitted(
    tmp_path, monkeypatch, notice
):
    body = request(
        result={
            "success": True,
            "count": 1,
            "jobs": [{"name": "private job"}],
            **notice,
        }
    )
    sent = dispatch(tmp_path, monkeypatch, body)
    assert json.loads(sent["messages"][1]["content"]) == {
        "success": True,
        "count": 1,
        "jobs_omitted": True,
    }
    assert "Private host" not in repr(sent)


@pytest.mark.parametrize(
    "notice",
    [
        {"gateway_running": "yes"},
        {"warning": {"raw": "untrusted"}},
    ],
)
def test_malformed_liveness_notices_remain_blocked(tmp_path, monkeypatch, notice):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    body = request(result={"success": True, "count": 1, "jobs": [{}], **notice})
    with pytest.raises(EgressBlocked):
        dispatch_authorized_agent_request(
            _agent(tmp_path), body, lambda _: pytest.fail("sent")
        )
