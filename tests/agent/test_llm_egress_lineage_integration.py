"""Synthetic producer-to-private-receipt lineage; no provider callbacks or live state."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import json
from asyncio import CancelledError
import threading
from contextvars import copy_context
from copy import deepcopy

import pytest

from agent import llm_egress_request_diagnostics as diagnostics
from agent.turn_request_assembly import assemble_api_request
from tests.agent.test_turn_context import _FakeAgent, _build
from tests.agent.test_llm_egress_refusal_origins import _agent, _blocked_receipt, _ENCODED


@contextmanager
def admitted_agent(tmp_path, history, current="clean current"):
    agent = _FakeAgent()
    agent.__dict__.update(_agent(tmp_path).__dict__)
    agent._persist_disabled = True
    authority = SimpleNamespace(profile_id="profile")
    source = object()
    scope = diagnostics.DiagnosticScope(
        admission_id="a" * 32, authority=authority, profile_id="profile",
        logical_session_id=agent.session_id, source=source, execution_generation=1,
    )
    scope.verify_claimed_turn(authority, agent.session_id, 1, source, agent.session_id)
    scope.bind_turn_agent(agent, agent.session_id, source)
    token = diagnostics.activate(scope)
    try:
        with patch("agent.auxiliary_client.set_runtime_main"):
            ctx = _build(agent, user_message=current, conversation_history=history)
        agent._current_api_request_id = agent._current_turn_id + ":api:0"
        agent._should_sanitize_tool_calls = lambda: False
        agent._copy_reasoning_content_for_api = lambda source, target: None
        agent._sanitize_api_messages = lambda rows: rows
        from agent.agent_runtime_helpers import drop_thinking_only_and_merge_users
        agent._drop_thinking_only_and_merge_users = drop_thinking_only_and_merge_users
        agent._use_prompt_caching = False
        yield agent, ctx, scope
    finally:
        scope.close()
        diagnostics.reset(token)


def assembled(agent, ctx):
    with patch("agent.conversation_loop._midturn_request_pressure_tokens", return_value=0), \
         patch("agent.conversation_loop._pressure_with_real_floor", return_value=0):
        return assemble_api_request(
            agent, messages=ctx.messages, current_turn_user_idx=ctx.current_turn_user_idx,
            _ext_prefetch_cache="", _plugin_user_context="", moa_config=None,
            active_system_prompt="SYSTEM", original_user_message=ctx.original_user_message,
            pending_moa_prepared_request=None, request_logger=SimpleNamespace(),
        )


def _candidates(private):
    return [finding for finding in private["findings"] if finding["basis"] == "candidate"]


def test_trusted_constructor_through_assembly_binds_prior_candidate(tmp_path):
    history = [{"role": "user", "content": _ENCODED},
               {"role": "assistant", "content": "clean answer"}]
    with admitted_agent(tmp_path, history) as (agent, ctx, _scope):
        result = assembled(agent, ctx)
        assert getattr(result, "_diagnostic_assembly", None) is not None
        from agent.llm_egress_lineage import assembly_context
        with assembly_context(result._diagnostic_assembly):
            decision, receipt = _blocked_receipt(agent, {"messages": result.api_messages})
        private = receipt["refusal_request_v1"]
        assert decision.reason_codes == ("base64_payload",)
        assert private["lineage_status"] == "supported"
        assert _candidates(private) == [{
            "reason": "base64_payload", "basis": "candidate", "scan_stage": "other",
            "carrier": "effective_message", "message_index": 1, "temporal": "prior_turn",
        }]
        assert private["findings"][0]["temporal"] == "unknown"
        assert private["findings"][0]["basis"] == "final_scan"
        assert _ENCODED not in json.dumps(private)


@pytest.mark.parametrize("case,expected", [
    ("current", ["current_turn"]), ("identical", ["prior_turn", "current_turn"]),
    ("merge", ["mixed"]), ("clone", ["current_turn"]),
    ("alias", ["unknown", "unknown"]), ("bool", ["unknown"]),
    ("replaced", ["unknown"]), ("barrier", ["unknown"]),
    ("override", ["current_turn", "unknown"]),
])
def test_temporal_candidates_follow_carriers_without_text_matching(tmp_path, case, expected):
    from agent.llm_egress_lineage import assembly_context, barrier
    from agent.conversation_loop import _clone_message_for_send
    from agent.transports.chat_completions import ChatCompletionsTransport
    history = [{"role": "user", "content": _ENCODED},
               {"role": "assistant", "content": "clean"}] if case == "identical" else []
    if case == "merge":
        history = [{"role": "user", "content": _ENCODED}]
    with admitted_agent(tmp_path, history, current=_ENCODED) as (agent, ctx, _scope):
        if case == "alias":
            ctx.messages.insert(0, ctx.messages[-1])
            ctx.messages.insert(1, {"role": "assistant", "content": "clean"})
            ctx.current_turn_user_idx = 2
        elif case == "bool":
            ctx.current_turn_user_idx = False
        elif case == "replaced":
            ctx.messages = deepcopy(ctx.messages)
        result = assembled(agent, ctx)
        with assembly_context(result._diagnostic_assembly):
            rows = result.api_messages
            if case == "barrier":
                barrier()  # an arbitrary mutator may preserve every object identity
            if case == "clone":
                rows = [_clone_message_for_send(row) for row in rows]
                rows[-1]["timestamp"] = 1  # force the actual chat conversion clone path
                transport = ChatCompletionsTransport()
                rows = transport.convert_messages(rows, model="gpt-5")
                rows = transport.build_kwargs("gpt-5", rows)["messages"]
            body = {"messages": rows}
            if case == "override":
                body["extra_body"] = {"messages": [{"role": "user", "content": _ENCODED}]}
            before = deepcopy(body)
            _, receipt = _blocked_receipt(agent, body)
            assert body == before
        candidates = _candidates(receipt["refusal_request_v1"])
        assert [c["temporal"] for c in candidates] == expected
        if case == "override":
            assert [c["carrier"] for c in candidates] == ["shadowed", "effective_message"]


def test_retry_ordinals_new_assemblies_and_unknown_override_cleanup(tmp_path):
    from agent.llm_egress_lineage import assembly_context
    with admitted_agent(tmp_path, [], current=_ENCODED) as (agent, ctx, scope):
        first = assembled(agent, ctx)
        with assembly_context(first._diagnostic_assembly):
            _, one = _blocked_receipt(agent, {"messages": first.api_messages})
            _, two = _blocked_receipt(agent, {"messages": first.api_messages})
        second = assembled(agent, ctx)
        assert first._diagnostic_assembly.closed
        assert not first._diagnostic_assembly.table._rows
        with assembly_context(first._diagnostic_assembly):
            _, stale = _blocked_receipt(agent, {"messages": first.api_messages})
        with assembly_context(second._diagnostic_assembly, close=True):
            _, three = _blocked_receipt(agent, {
                "messages": second.api_messages, "extra_body": {"messages": None},
            })
        assert [r["refusal_request_v1"]["authorization_ordinal"] for r in (one, two, stale, three)] == [1, 2, 3, 4]
        assert stale["refusal_request_v1"]["lineage_status"] == "unavailable"
        assert _candidates(three["refusal_request_v1"])[0]["carrier"] == "shadowed"
        assert three["refusal_request_v1"]["lineage_status"] == "partial"
        assert not second._diagnostic_assembly.table._rows
        assert not scope._attempts


def test_two_concurrent_turns_keep_private_lineage_and_owner_separate(tmp_path):
    from agent.llm_egress_lineage import assembly_context
    from concurrent.futures import ThreadPoolExecutor
    ready = threading.Barrier(2)

    def run(index):
        history = [{"role": "user", "content": _ENCODED},
                   {"role": "assistant", "content": "clean"}] if index == 0 else []
        with admitted_agent(tmp_path / str(index), history,
                            current="clean" if index == 0 else _ENCODED) as (agent, ctx, scope):
            scope.admission_id = str(index + 1) * 32
            result = assembled(agent, ctx)
            with assembly_context(result._diagnostic_assembly):
                ready.wait(timeout=10)
                _, receipt = _blocked_receipt(agent, {"messages": result.api_messages})
            return receipt["refusal_request_v1"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(run, range(2)))
    assert (a["admission_ref"], b["admission_ref"]) == ("1" * 32, "2" * 32)
    assert _candidates(a)[0]["temporal"] == "prior_turn"
    assert _candidates(b)[0]["temporal"] == "current_turn"


def test_detached_context_after_failed_admission_and_close_cannot_publish(tmp_path):
    from agent.llm_egress_lineage import assembly_context
    from agent.llm_egress_firewall import TypedOutboundRequest
    from agent.llm_egress_lineage import snapshot
    with admitted_agent(tmp_path, [], current=_ENCODED) as (agent, ctx, scope):
        result = assembled(agent, ctx)
        with assembly_context(result._diagnostic_assembly):
            _, receipt = _blocked_receipt(agent, {"messages": result.api_messages})
            assert receipt["refusal_request_v1"]["request_binding"] == "exact"
            copied = copy_context()
        scope.close()
        request = TypedOutboundRequest({}, agent.session_id, agent._current_turn_id,
                                       agent._current_api_request_id, "digest")
        assert copied.run(snapshot, agent, request) is None
        assert not result._diagnostic_assembly.table._rows
        assert scope._constructed_turn is None


@pytest.mark.parametrize("invalidation", ["close", "barrier"])
def test_assembly_close_between_snapshot_and_binding_revokes_candidates(tmp_path, monkeypatch, invalidation):
    from agent.llm_egress_lineage import assembly_context, barrier
    ready, resume = threading.Event(), threading.Event()
    receipts = []
    with admitted_agent(tmp_path, [], current=_ENCODED) as (agent, ctx, scope):
        result = assembled(agent, ctx)
        original_bind = scope.bind_request

        def delayed_bind(*args, **kwargs):
            ready.set()
            assert resume.wait(timeout=10)
            return original_bind(*args, **kwargs)

        monkeypatch.setattr(scope, "bind_request", delayed_bind)

        def run():
            with assembly_context(result._diagnostic_assembly):
                _, receipt = _blocked_receipt(agent, {"messages": result.api_messages})
                receipts.append(receipt["refusal_request_v1"])

        thread = threading.Thread(target=copy_context().run, args=(run,))
        thread.start()
        assert ready.wait(timeout=10)
        with scope._lock:
            if invalidation == "close":
                result._diagnostic_assembly.close()
            else:
                with assembly_context(result._diagnostic_assembly):
                    barrier()
        resume.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert receipts[0]["lineage_status"] == "unavailable"
        assert "candidates" not in receipts[0]


@pytest.mark.parametrize("role", ["assistant", "tool"])
def test_real_tool_sanitizer_preserves_prior_carrier_through_clones(tmp_path, role):
    from agent.llm_egress_lineage import assembly_context
    from agent.agent_runtime_helpers import sanitize_api_messages
    history = [
        {"role": "user", "content": "prior question"},
        {"role": "assistant", "content": _ENCODED if role == "assistant" else "",
         "tool_calls": [{"id": "synthetic-call", "type": "function", "function": {
             "name": "synthetic_tool", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "synthetic-call", "name": "wrong-name",
         "content": _ENCODED if role == "tool" else "clean"},
    ]
    with admitted_agent(tmp_path, history) as (agent, ctx, _scope):
        agent._sanitize_api_messages = sanitize_api_messages
        result = assembled(agent, ctx)
        with assembly_context(result._diagnostic_assembly):
            _, receipt = _blocked_receipt(agent, {"messages": result.api_messages})
        assert _candidates(receipt["refusal_request_v1"])[0]["temporal"] == "prior_turn"


@pytest.mark.parametrize("error", [RuntimeError, InterruptedError, TimeoutError, CancelledError])
def test_turn_finally_revokes_assembly_on_failure(tmp_path, monkeypatch, error):
    from agent.conversation_loop import run_conversation
    with admitted_agent(tmp_path, [], current=_ENCODED) as (agent, ctx, scope):
        result = assembled(agent, ctx)
        def fail(*args, **kwargs):
            raise error("synthetic-sensitive-canary")
        monkeypatch.setattr("agent.conversation_loop._run_conversation_turn", fail)
        with pytest.raises(error):
            run_conversation(agent, "synthetic input")
        assert result._diagnostic_assembly.closed
        assert not result._diagnostic_assembly.table._rows
        assert scope._constructed_turn is None


def test_failed_nested_admission_does_not_borrow_outer_lineage(tmp_path):
    from agent.llm_egress_lineage import assembly_context
    with admitted_agent(tmp_path, [], current=_ENCODED) as (agent, ctx, scope):
        result = assembled(agent, ctx)
        with assembly_context(result._diagnostic_assembly):
            failed, tokens = diagnostics.begin_admission(None, None, None, None)
            try:
                _, receipt = _blocked_receipt(agent, {"messages": result.api_messages})
                assert "refusal_request_v1" not in receipt
            finally:
                diagnostics.end_admission(failed, tokens)
        assert diagnostics.current_scope() is scope


@pytest.mark.parametrize("mutation", ["none", "middleware", "hook", "context_engine"])
def test_actual_request_builder_treats_arbitrary_mutation_as_unknown(tmp_path, monkeypatch, mutation):
    from agent.llm_egress_lineage import assembly_context
    from agent.turn_api_request import build_api_request
    from agent.transports.chat_completions import ChatCompletionsTransport
    from hermes_cli.middleware import RequestMiddlewareResult
    with admitted_agent(tmp_path, [], current=_ENCODED) as (agent, ctx, _scope):
        if mutation == "context_engine":
            # Arbitrary code returns the same list and objects. Identity alone is insufficient.
            agent.context_compressor.select_context = lambda rows, **kwargs: rows
        result = assembled(agent, ctx)
        agent._reset_stream_delivery_tracking = lambda: None
        agent._reapply_reasoning_echo_for_provider = lambda rows: None
        agent._empty_content_retries = 0
        agent._image_rejecting_models = set()
        agent._force_ascii_payload = False
        agent._is_copilot_url = lambda: False
        agent._api_request_payload_for_hook = lambda kwargs: kwargs
        agent._build_api_kwargs = lambda rows, **kwargs: ChatCompletionsTransport().build_kwargs(
            "gpt-5", ChatCompletionsTransport().convert_messages(rows), tools=None)
        monkeypatch.setattr("hermes_cli.plugins.has_middleware", lambda name: mutation == "middleware")
        monkeypatch.setattr("hermes_cli.middleware.apply_llm_request_middleware",
                            lambda payload, **kwargs: RequestMiddlewareResult(payload, payload))
        monkeypatch.setattr("hermes_cli.lifecycle.has_hook", lambda name: mutation == "hook")
        monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", lambda *args, **kwargs: None)
        with assembly_context(result._diagnostic_assembly):
            request = build_api_request(
                agent, api_messages=result.api_messages, _moa_prepared_request=None,
                tools_for_api=[], system_message="SYSTEM", messages=ctx.messages,
                original_user_message=ctx.original_user_message, approx_tokens=1, total_chars=1,
                retry_count=0, api_call_count=0, api_request_id=agent._current_api_request_id,
                api_start_time=0, effective_task_id="test", turn_id=ctx.turn_id,
            )
            assert not any("diagnostic" in key for key in request.api_kwargs)
            _, receipt = _blocked_receipt(agent, request.api_kwargs)
        temporal = _candidates(receipt["refusal_request_v1"])[0]["temporal"]
        assert temporal == ("current_turn" if mutation == "none" else "unknown")


def test_cache_planning_and_lazy_observer_preserve_authorized_payload_bytes(tmp_path, monkeypatch):
    from agent.llm_egress_lineage import assembly_context
    from agent.prompt_caching import build_prompt_cache_plan
    from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
    with admitted_agent(tmp_path, [], current="ordinary synthetic task") as (agent, ctx, _scope):
        result = assembled(agent, ctx)
        with assembly_context(result._diagnostic_assembly):
            plan = build_prompt_cache_plan(result.api_messages, [], native_anthropic=False)
            body = {"messages": plan.messages}
            allowed, authorization = authorize_agent_sdk_kwargs(agent, body)
            monkeypatch.setattr("agent.llm_egress_lineage.snapshot",
                                lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("synthetic-sensitive-canary")))
            allowed_again, authorization_again = authorize_agent_sdk_kwargs(agent, body)
        assert authorization.payload_bytes == authorization_again.payload_bytes
        assert allowed == allowed_again
        assert "synthetic-sensitive-canary" not in (agent._llm_egress_state_dir / "llm-egress-receipts.jsonl").read_text()


def test_raising_observer_preserves_real_block_and_never_logs_its_exception(tmp_path, monkeypatch, caplog):
    from agent.llm_egress_lineage import assembly_context
    observed = []
    with admitted_agent(tmp_path, [], current=_ENCODED) as (agent, ctx, _scope):
        result = assembled(agent, ctx)
        with assembly_context(result._diagnostic_assembly):
            before, _ = _blocked_receipt(agent, {"messages": result.api_messages})

            def fail(*args, **kwargs):
                observed.append(True)
                raise ValueError("synthetic-sensitive-canary")

            monkeypatch.setattr("agent.llm_egress_lineage.snapshot", fail)
            after, receipt = _blocked_receipt(agent, {"messages": result.api_messages})
        assert observed == [True]
        assert before.allowed is after.allowed is False
        assert before.reason_codes == after.reason_codes
        assert before.payload_sha256 == after.payload_sha256
        assert "refusal_request_v1" not in receipt
        assert "synthetic-sensitive-canary" not in json.dumps(receipt)
        assert "synthetic-sensitive-canary" not in caplog.text


def test_candidate_observation_follows_real_renderer_and_respects_finite_schema(tmp_path, monkeypatch):
    from agent.llm_egress_firewall import LLMEgressFirewall
    from agent import llm_egress_lineage as lineage
    rendered = []
    original_render = LLMEgressFirewall._construct_typed_request
    original_snapshot = lineage.snapshot

    def render(*args, **kwargs):
        result = original_render(*args, **kwargs)
        rendered.append(True)
        return result

    def observe(*args, **kwargs):
        assert rendered  # candidate rescan cannot run before the actual renderer
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(LLMEgressFirewall, "_construct_typed_request", render)
    monkeypatch.setattr(lineage, "snapshot", observe)
    history = [row for _ in range(20) for row in (
        {"role": "user", "content": _ENCODED}, {"role": "assistant", "content": "clean"})]
    with admitted_agent(tmp_path, history) as (agent, ctx, _scope):
        result = assembled(agent, ctx)
        with lineage.assembly_context(result._diagnostic_assembly):
            _, receipt = _blocked_receipt(agent, {"messages": result.api_messages})
        private = receipt["refusal_request_v1"]
        assert len(private["findings"]) == 16
        assert private["findings_overflow"] is True
        assert private["lineage_overflow"] is False
        assert private["lineage_status"] == "supported"
        for finding in private["findings"]:
            assert set(finding) <= {"reason", "scan_stage", "basis", "carrier", "temporal", "message_index"}
        for finding in private["final_scan_attribution"]:
            assert set(finding) <= {"reason", "carrier", "message_index"}
        assert set(private) <= {"version", "admission_ref", "authority_epoch", "execution_generation",
                                "admission_sequence", "authorization_ordinal", "admission_binding",
                                "request_binding", "lineage_status", "findings", "findings_overflow",
                                "lineage_overflow", "final_scan_attribution"}
