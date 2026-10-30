"""Public lifecycle assignments must reach the real child's model request."""
from copy import deepcopy
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.subagent_lifecycle import SubagentLaunchRequest, SubagentLifecycleError, SubagentLifecycleService
from agent.worker_contract import ContextProfile, JobContract, WorkerConstitution, WorkforceGovernance


@pytest.fixture
def launch_context(monkeypatch, tmp_path):
    import agent.subagent_lifecycle as lifecycle
    import tools.delegate_tool as delegate

    submitted = []
    def submit(fn, *args):
        submitted.append(args[0])
        return Future()

    monkeypatch.setattr(lifecycle._EXECUTOR, "submit", submit)
    monkeypatch.setattr(delegate, "_load_config", dict)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda *args, **kw: {})
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda *args, **kw: {})
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda *args, **kw: [])
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *args, **kw: {})
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None), finish_reason="stop")],
        model="test/model", usage=None,
    )
    factory = MagicMock(return_value=client)
    client.fixture_constructor = factory
    captured_requests = []
    def complete(**kwargs):
        captured_requests.append(deepcopy(kwargs))
        return client.chat.completions.create.return_value
    client.chat.completions.create.side_effect = complete
    client.fixture_requests = captured_requests
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", factory)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("PROJECT_GUIDANCE_CANARY")
    (workspace / "SOUL.md").write_text("PRIVATE_IDENTITY_CANARY")
    parent = SimpleNamespace(
        session_id="context-parent", enabled_toolsets=["file"], disabled_toolsets=[],
        model="test/model", provider="openrouter", api_mode="chat_completions",
        api_key="fixture-only", base_url="https://openrouter.ai/api/v1",
        _session_db=None, _delegate_depth=0, _active_children=[],
        cwd=str(workspace),
    )
    service = SubagentLifecycleService(lambda: parent)
    yield service, submitted, client
    for record in submitted:
        record.agent.close()
        lifecycle._REGISTRY.records.pop(record.handle.subagent_id, None)


def _constitution():
    return WorkerConstitution(
        profile="researcher", values=("RULE_CANARY",), authority="research-only",
        forbidden_actions=("NO_SIDE_EFFECT_CANARY",), required_evidence=("research",),
    )


def _contract():
    now = datetime.now(UTC)
    return JobContract(
        name="fixture", worker_profile="researcher", job="inspect", scope=("SCOPE_CANARY",),
        authority="research-only", obligations=("OBLIGATION_CANARY",),
        granted_at=(now - timedelta(minutes=1)).isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
    )


def test_launch_assignment_reaches_first_child_request(launch_context):
    service, submitted, client = launch_context
    service.launch(SubagentLaunchRequest(
        goal="inspect only", context="USER_CONTEXT_CANARY", constitution=_constitution(), job_contract=_contract(),
        governance=WorkforceGovernance(context=ContextProfile(profile="researcher", assumptions=("GOVERNANCE_CANARY",), assumption_warnings=("VERIFY_CANARY",))),
    ))
    child = submitted[0].agent
    child._disable_streaming = True
    child.compression_enabled = False
    child.save_trajectories = False
    child._fallback_chain = []
    child.run_conversation("inspect only")
    messages = client.fixture_requests[0]["messages"]
    prompt = str(messages)
    for required in ("RULE_CANARY", "SCOPE_CANARY", "OBLIGATION_CANARY", "NO_SIDE_EFFECT_CANARY", "USER_CONTEXT_CANARY", "GOVERNANCE_CANARY"):
        assert required in prompt
    assert "PRIVATE_IDENTITY_CANARY" not in prompt
    assert "PROJECT_GUIDANCE_CANARY" in prompt


def test_launch_rejects_oversized_composed_context_before_construction(launch_context):
    service, submitted, client = launch_context
    with pytest.raises(SubagentLifecycleError, match="context"):
        service.launch(SubagentLaunchRequest(goal="inspect", context="x" * 32000, constitution=_constitution()))
    assert submitted == []
    client.chat.completions.create.assert_not_called()
    client.fixture_constructor.assert_not_called()


def test_launch_without_contract_preserves_context(launch_context):
    service, submitted, client = launch_context
    service.launch(SubagentLaunchRequest(goal="inspect", context="UNCHANGED_CONTEXT_CANARY"))
    child = submitted[0].agent
    child._disable_streaming = True
    child.compression_enabled = False
    child.save_trajectories = False
    child._fallback_chain = []
    child.run_conversation("inspect")
    prompt = str(client.fixture_requests[0]["messages"])
    assert "UNCHANGED_CONTEXT_CANARY" in prompt
    assert "GOVERNED WORKFORCE ASSIGNMENT:" not in prompt
    assert "PROJECT_GUIDANCE_CANARY" in prompt
    assert "PRIVATE_IDENTITY_CANARY" not in prompt
