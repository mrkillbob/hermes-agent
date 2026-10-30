"""Real conversation-loop wiring with an in-process synthetic provider and no SDK I/O."""

from functools import wraps
from types import SimpleNamespace
from unittest.mock import patch

from agent import conversation_loop, llm_egress_lineage, llm_egress_request_diagnostics as diagnostics
from agent.llm_egress_firewall import LLMEgressFirewall
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from tests.agent.test_proactive_prune_loop_wiring import _stop_response


def test_loop_binds_late_request_id_and_revokes_each_retry_attempt(tmp_path, monkeypatch):
    from run_agent import AIAgent

    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        agent = AIAgent(
            provider="custom", model="synthetic-model", base_url="https://llm.example.test/v1",
            api_key="synthetic-not-a-credential", session_id="synthetic-session",
            quiet_mode=True, skip_context_files=True, skip_memory=True, max_iterations=2,
        )
    agent._cached_system_prompt = "Synthetic bounded system context."
    agent._use_prompt_caching = False
    agent._disable_streaming = True
    agent.compression_enabled = False
    agent.save_trajectories = False
    agent._persist_disabled = True
    agent._api_max_retries = 2
    agent._llm_egress_state_dir = tmp_path / "egress"
    monkeypatch.setattr(agent, "_try_refresh_env_client_credentials", lambda: None)
    monkeypatch.setattr(agent, "_persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent, "_save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent, "_cleanup_task_resources", lambda *args, **kwargs: None)

    authority, source = SimpleNamespace(profile_id="synthetic-profile"), object()
    scope = diagnostics.DiagnosticScope(
        admission_id="a" * 32, authority=authority, profile_id=authority.profile_id,
        logical_session_id=agent.session_id, source=source, execution_generation=1,
    )
    assert scope.verify_claimed_turn(authority, agent.session_id, 1, source, agent.session_id)
    assert scope.bind_turn_agent(agent, agent.session_id, source)
    token = diagnostics.activate(scope)
    assemblies, attempts, observations, provider_calls = [], [], [], []
    original_assemble = conversation_loop.assemble_api_request
    original_append = LLMEgressFirewall._append_receipt

    @wraps(original_assemble)
    def assemble(*args, **kwargs):
        assert agent._current_api_request_id == ""  # The loop assigns it AFTER assembly.
        result = original_assemble(*args, **kwargs)
        assemblies.append(result._diagnostic_assembly)
        return result

    def append(firewall, decision, grants, request=None):
        active_scope, attempt = diagnostics._attempt_context.get()
        assert active_scope is scope
        assert attempt.request is request
        assert attempt.owner == (agent.session_id, agent._current_turn_id,
                                 agent._current_api_request_id, request.policy_digest)
        assembly = llm_egress_lineage._active.get()
        assert assembly is assemblies[-1]
        assert assembly.table.get(request.payload["messages"][-1]) == "current_turn"
        observations.append((attempt.ordinal, request.request_id))
        attempts.append(attempt)
        return original_append(firewall, decision, grants, request)

    class SyntheticRateLimit(Exception):
        status_code = 429

    def provider(kwargs):
        wire, authorization = authorize_agent_sdk_kwargs(agent, kwargs)
        assert authorization.allowed
        assert wire == kwargs
        assert scope._attempts == {}
        assert diagnostics._attempt_context.get() is None
        assert all(attempt.request is None and attempt.agent is None and attempt.lineage is None
                   for attempt in attempts)
        provider_calls.append(True)
        if len(provider_calls) == 1:
            raise SyntheticRateLimit("Synthetic transient rate limit; retry the same request.")
        return _stop_response()

    monkeypatch.setattr(conversation_loop, "assemble_api_request", assemble)
    monkeypatch.setattr(LLMEgressFirewall, "_append_receipt", append)
    monkeypatch.setattr(agent, "_interruptible_api_call", provider)
    try:
        result = agent.run_conversation("Synthetic current task.", conversation_history=[], task_id="synthetic-task")
        assert result["completed"] is True
        assert result["final_response"] == "done"
        assert provider_calls == [True, True]
        assert len(assemblies) == 1
        assert [ordinal for ordinal, _ in observations] == [1, 2]
        assert observations[0][1] == observations[1][1] == agent._current_api_request_id
        assert agent._current_api_request_id.startswith(agent._current_turn_id + ":api:")
        assert assemblies[0].closed and not assemblies[0].table._rows
        assert scope._attempts == {} and scope._constructed_turn is None and scope._assembly is None
        assert diagnostics._attempt_context.get() is None
        assert llm_egress_lineage._active.get() is None
    finally:
        scope.close()
        diagnostics.reset(token)
        agent.close()
