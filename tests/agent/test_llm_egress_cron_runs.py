"""Protected cron run history replays statuses and stamps, never IDs or error text."""

import json

import pytest

from agent.llm_egress_cron import _runs_projection
from agent.llm_egress_firewall import EgressBlocked
from tests.agent.test_llm_egress_cron_list_projection import dispatch
from tools.cronjob_runs import action_runs

STAMP = "2026-10-16T01:00:43.938364-07:00"
RESULT = {
    "success": True,
    "count": 2,
    "runs": [
        {"status": "failed", "claimed_at": STAMP, "finished_at": "2026-10-16T01:00:44-07:00"},
        {"status": "completed", "claimed_at": "2026-10-15T01:00:02-07:00"},
    ],
}


def request(*, direct=False, result=None, arguments=None):
    arguments = arguments or {"action": "runs", "job_id": "safety-policy-scan"}
    function = {"name": "cronjob_manage", "arguments": json.dumps(arguments)}
    if not direct:
        function = {
            "name": "tool_call",
            "arguments": json.dumps({"calls": [{"name": "cronjob_manage", "arguments": arguments}]}),
        }
    return {
        "model": "test-model",
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": "call_runs", "type": "function", "function": function}]},
            {"role": "tool", "tool_call_id": "call_runs", "content": json.dumps(result or RESULT)},
        ],
    }


@pytest.mark.parametrize("direct", [False, True])
def test_exact_runs_result_reaches_sender(tmp_path, monkeypatch, direct):
    sent = dispatch(tmp_path, monkeypatch, request(direct=direct))
    assert json.loads(sent["messages"][1]["content"]) == RESULT


@pytest.mark.parametrize("direct", [False, True])
def test_runs_for_base64_shaped_kebab_job_name_reaches_sender(tmp_path, monkeypatch, direct):
    # hermes-upstream-sync is rejected by the free-text sanitizer by chance; it is a plain
    # job name, and the call bytes must reach the sender exactly as the model wrote them.
    arguments = {"action": "runs", "job_id": "hermes-upstream-sync"}
    body = request(direct=direct, arguments=arguments)
    expected = body["messages"][0]["tool_calls"][0]["function"]["arguments"]
    sent = dispatch(tmp_path, monkeypatch, body)
    assert sent["messages"][0]["tool_calls"][0]["function"]["arguments"] == expected
    assert json.loads(sent["messages"][1]["content"]) == RESULT


@pytest.mark.parametrize("job_id", ["c2VjcmV0LXBheWxvYWQ=", "aGVybWVzLXVwc3RyZWFt", "Zm9vYmFyYmF6cXV4"])
def test_encoded_value_as_job_id_stays_blocked(tmp_path, monkeypatch, job_id):
    with pytest.raises(EgressBlocked):
        dispatch(tmp_path, monkeypatch, request(arguments={"action": "runs", "job_id": job_id}))


@pytest.mark.parametrize(
    "result",
    [
        {**RESULT, "runs": [{**RESULT["runs"][0], "id": "b27ce763bcbf411e9a1a2bafb973f6d2"}, RESULT["runs"][1]]},
        {**RESULT, "runs": [{**RESULT["runs"][0], "error": "HTTP 429: rate limit"}, RESULT["runs"][1]]},
        {**RESULT, "runs": [{**RESULT["runs"][0], "status": "weird"}, RESULT["runs"][1]]},
        {**RESULT, "runs": [{**RESULT["runs"][0], "claimed_at": "c2VjcmV0LXBheWxvYWQ="}, RESULT["runs"][1]]},
        {**RESULT, "count": 3},
        {**RESULT, "job": "safety-policy-scan"},
        {"success": False, "count": 0, "runs": []},
    ],
)
def test_runs_result_with_anything_else_stays_blocked(tmp_path, monkeypatch, result):
    assert _runs_projection(json.dumps(result)) is None
    with pytest.raises(EgressBlocked):
        dispatch(tmp_path, monkeypatch, request(result=result))


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "runs"},
        {"action": "runs", "job_id": "safety-policy-scan", "limit": 5},
        {"action": "runs", "job_id": "../etc/passwd"},
        {"action": "runs", "job_id": "x" * 65},
        {"action": "runs", "job_id": 7},
    ],
)
def test_runs_call_must_be_exact(tmp_path, monkeypatch, arguments):
    with pytest.raises(EgressBlocked):
        dispatch(tmp_path, monkeypatch, request(arguments=arguments))


def test_action_runs_drops_ids_and_error_text(monkeypatch):
    records = [
        {
            "id": "b27ce763bcbf411e9a1a2bafb973f6d2",
            "job_id": "safety-policy-scan",
            "status": "failed",
            "claimed_at": STAMP,
            "finished_at": "2026-10-16T01:00:44-07:00",
            "error": "HTTP 429: You've reached this model's current fair-share rate limit",
        },
        {"status": "bogus", "claimed_at": "not a stamp", "finished_at": None},
    ]
    monkeypatch.setattr("cron.executions.list_executions", lambda **_: records)
    out = action_runs({"id": "safety-policy-scan"}, {})
    assert json.loads(out) == {
        "success": True,
        "count": 1,
        "runs": [{"status": "failed", "claimed_at": STAMP, "finished_at": "2026-10-16T01:00:44-07:00"}],
    }
