"""Scheduled-job framing stays compatible with strict remote egress scanning."""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import pytest

from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance import SourceProvenanceRegistry


def _agent(tmp_path):
    return SimpleNamespace(
        provider="custom",
        model="test-model",
        base_url="https://llm.example.test/v1",
        api_mode="chat_completions",
        session_id="session-1",
        _current_turn_id="turn-1",
        _current_api_request_id="req-1",
        _llm_egress_policy_digest=sha256(b"policy-1").hexdigest(),
        _llm_egress_state_dir=tmp_path,
        _source_provenance_registry=SourceProvenanceRegistry(),
    )


@pytest.mark.parametrize("task_prompt", ["", "Summarize the public project status."])
def test_cron_framing_with_empty_or_benign_task_passes_remote_egress(tmp_path, task_prompt):
    """Fixed cron framing and a benign task must pass the actual egress boundary."""
    from cron.scheduler_prompt import _build_job_prompt

    prompt = _build_job_prompt({"id": "synthetic-egress-test", "prompt": task_prompt})
    authorized, receipt = authorize_agent_sdk_kwargs(
        _agent(tmp_path),
        {"model": "test-model", "messages": [{"role": "user", "content": prompt}]},
    )

    assert authorized["messages"][0]["content"] == prompt
    assert receipt.decision.allowed


@pytest.mark.parametrize(
    ("user_text", "reason"),
    [
        ("DELIVERY", "base64_payload"),
        ("EXISTING", "base64_payload"),
        ("c2VjcmV0LXBheWxvYWQ=", "base64_payload"),
        ("SECRET_TOKEN=super-secret-value", "secret_detected"),
    ],
)
def test_cron_task_text_does_not_inherit_framing_exemptions(tmp_path, user_text, reason):
    """User task content remains subject to the normal fail-closed egress scans."""
    from cron.scheduler_prompt import _build_job_prompt

    prompt = _build_job_prompt({"id": "synthetic-egress-test", "prompt": user_text})

    with pytest.raises(EgressBlocked) as exc_info:
        authorize_agent_sdk_kwargs(
            _agent(tmp_path),
            {"model": "test-model", "messages": [{"role": "user", "content": prompt}]},
        )

    assert reason in exc_info.value.decision.reason_codes
