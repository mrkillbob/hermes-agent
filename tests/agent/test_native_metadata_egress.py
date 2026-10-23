"""Model-free actual owner/dispatch/commit/provider-authorization metadata contracts."""
import json
import uuid
from types import SimpleNamespace

import pytest

from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from tests.agent.test_native_metadata_capture import _owner, TOOL
from tests.agent.test_tool_activity_heartbeat import _make_agent


def _frames(protocol, args, output, *, call_id="call-1", name=TOOL):
    arguments = json.dumps(args, sort_keys=True, separators=(",", ":"))
    if protocol == "chat":
        return {"messages": [{"role": "assistant", "tool_calls": [{"id": call_id,
            "type": "function", "function": {"name": name, "arguments": arguments}}]},
            {"role": "tool", "tool_call_id": call_id, "content": output}]}
    if protocol == "anthropic":
        return {"messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": call_id,
            "name": name, "input": args}]}, {"role": "user", "content": [{"type": "tool_result",
            "tool_use_id": call_id, "content": output}]}]}
    return {"input": [{"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments},
                      {"type": "function_call_output", "call_id": call_id, "output": output}]}


def _actual_dispatch_and_commit(fixture, monkeypatch, *, name=TOOL, call_id="call-1", execute=None, budget=None):
    from agent import tool_executor
    from tools.budget_config import DEFAULT_BUDGET

    agent = _make_agent(monkeypatch)
    for key, value in vars(fixture.agent).items():
        setattr(agent, key, value)
    fixture.agent = agent
    agent._tool_guardrails = SimpleNamespace(before_call=lambda *_a: SimpleNamespace(allows_execution=True))
    monkeypatch.setattr("hermes_cli.plugins._dispatch_pre_tool_call_hooks", lambda *_a, **_kw: (None, None))
    outcome = tool_executor._run_agent_tool_execution_middleware(agent, function_name=name,
        function_args=fixture.args, effective_task_id="task", tool_call_id=call_id,
        execute=execute or (lambda _args: fixture.result))
    messages = []
    committed = tool_executor._commit_tool_result(agent, messages,
        tool_executor._ToolCallRef(name, fixture.args, "task", call_id, []), outcome.result,
        budget=budget or DEFAULT_BUDGET, tool_duration=0.0, is_error=False, blocked=outcome.blocked,
        effect_disposition="none")
    assert committed is not None
    return messages[-1]["content"]


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_actual_owner_call_and_committed_derived_ids_preserve_exact_wire_bytes(tmp_path, monkeypatch, protocol):
    from agent.native_metadata_capture import native_metadata_execution_scope

    with _owner() as f:
        f.agent.provider = "anthropic" if protocol == "anthropic" else "nous"
        f.agent.model = "offline-model"
        f.agent.base_url = "https://api.anthropic.com/v1" if protocol == "anthropic" else "https://inference-api.nousresearch.com/v1"
        f.agent.api_mode = {"chat": "chat_completions", "anthropic": "anthropic", "responses": "responses"}[protocol]
        f.agent._llm_egress_state_dir = tmp_path
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            # Real final authorization context, without an SDK callback or model call.
            _safe, initial = authorize_agent_sdk_kwargs(f.agent, {
                "messages": [{"role": "user", "content": "Bounded invented task."}]})
            assert initial.allowed
            content = _actual_dispatch_and_commit(f, monkeypatch)
            from agent.native_metadata_capture import captured_native_metadata
            from agent.native_metadata_egress import _presentation_projection, recipient_digest
            from agent.llm_egress_runtime import _route_for_agent
            record, = captured_native_metadata(f.agent)
            assert record.recipient_digest == recipient_digest(_route_for_agent(f.agent, None))
            assert record.next_provider_request_id == "turn-1:api:2"
            assert _presentation_projection(record, content, 262144) is not None
            f.agent._current_api_request_id = "turn-1:api:2"
            body = _frames(protocol, f.args, content)
            try:
                wire, receipt = authorize_agent_sdk_kwargs(f.agent, body)
                reasons = ()
            except EgressBlocked as error:
                wire, receipt, reasons = None, None, error.decision.reason_codes
            assert reasons == (), "Owned actual metadata still blocked: " + repr(reasons)
            assert receipt.allowed and wire == body
            assert receipt.decision.source_segment_count == 0


def _configure(f, tmp_path, protocol="chat"):
    f.agent.provider = "anthropic" if protocol == "anthropic" else "nous"
    f.agent.model = "offline-model"
    f.agent.base_url = "https://api.anthropic.com/v1" if protocol == "anthropic" else "https://inference-api.nousresearch.com/v1"
    f.agent.api_mode = {"chat": "chat_completions", "anthropic": "anthropic", "responses": "responses"}[protocol]
    f.agent._llm_egress_state_dir = tmp_path


def _initial(f):
    wire, authorization = authorize_agent_sdk_kwargs(f.agent, {
        "messages": [{"role": "user", "content": "Bounded invented task."}]})
    assert authorization.allowed
    return wire


def _replace_entry(f):
    from tools.registry import registry
    registry.deregister(TOOL, scope=f.scope)
    registry.register(TOOL, "replacement", {"name": TOOL}, lambda _args: None, scope=f.scope)


def _changed_argument_frames(protocol, args, content):
    changed = json.loads(json.dumps(args))
    changed["task"]["work_item_id"] = "other-work"
    return _frames(protocol, changed, content)


def _flag_result(protocol, body):
    actions = {
        "chat": lambda: body["messages"][-1].update(blocked=True),
        "anthropic": lambda: body["messages"][-1]["content"][0].update(is_error=True),
        "responses": lambda: body["input"][-1].update(status="failed"),
    }
    actions[protocol]()


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("bad", ["fallback", "policy", "entry", "arguments", "flags", "copy", "extra-base64", "wrapper-copy", "exact-secret", "name-secret", "id-prefix", "id-suffix"])
def test_owned_metadata_never_suppresses_other_final_guards(tmp_path, monkeypatch, protocol, bad):
    from agent.native_metadata_capture import native_metadata_execution_scope
    with _owner() as f:
        _configure(f, tmp_path, protocol)
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            if bad in {"extra-base64", "wrapper-copy", "id-prefix", "id-suffix"}:
                payload = json.loads(json.loads(f.result)["result"])
                payload["external"] = "U3ludGhldGljIGV4dGVybmFsIHBheWxvYWQ="
                if bad == "wrapper-copy":
                    from agent.tool_dispatch_helpers import _maybe_wrap_untrusted
                    payload["external"] = _maybe_wrap_untrusted(TOOL, payload["external"])
                if bad in {"id-prefix", "id-suffix"}:
                    extra = payload.pop("external")
                    payload["request_id"] = extra + payload["request_id"] if bad == "id-prefix" else payload["request_id"] + extra
                f.result = json.dumps({"result": json.dumps(payload)})
            content = _actual_dispatch_and_commit(f, monkeypatch)
            f.agent._current_api_request_id = "turn-1:api:2"
            body = _frames(protocol, f.args, content)
            context = SimpleNamespace(body=body, route=None)
            mutations = {
                "fallback": lambda: setattr(context, "route", SimpleNamespace(provider=f.agent.provider,
                    model="other-model", base_url=f.agent.base_url, api_mode=f.agent.api_mode)),
                "policy": lambda: setattr(f.agent, "_llm_egress_policy_digest", "b" * 64),
                "entry": lambda: _replace_entry(f),
                "arguments": lambda: setattr(context, "body", _changed_argument_frames(protocol, f.args, content)),
                "flags": lambda: _flag_result(protocol, body),
                "copy": lambda: body.update(unowned_copy=content),
                "exact-secret": lambda: monkeypatch.setattr("agent.llm_egress_runtime._exact_provider_secret_values", lambda: (f.request_id,)),
                "name-secret": lambda: monkeypatch.setattr("agent.llm_egress_runtime._exact_provider_secret_values", lambda: (TOOL,)),
            }
            mutations.get(bad, lambda: None)()
            with pytest.raises(EgressBlocked) as error:
                authorize_agent_sdk_kwargs(f.agent, context.body, route=context.route)
            assert error.value.decision.reason_codes
            if bad in {"exact-secret", "name-secret"}:
                assert "exact_secret" in " ".join(error.value.decision.reason_codes)


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_actual_read_ack_after_admitted_submit_across_same_owner_new_agent_turn(tmp_path, monkeypatch, protocol):
    from agent.native_metadata_capture import native_metadata_execution_scope
    with _owner() as f:
        _configure(f, tmp_path, protocol)
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            content = _actual_dispatch_and_commit(f, monkeypatch)
            f.agent._current_api_request_id = "turn-1:api:2"
            submit = _frames(protocol, f.args, content)
            wire, authorization = authorize_agent_sdk_kwargs(f.agent, submit)
            assert wire == submit and authorization.allowed
            f.agent = SimpleNamespace(**{key: value for key, value in vars(f.agent).items()
                if not key.startswith("_native_metadata")})
            f.agent._current_turn_id = "turn-2"
            f.agent._current_api_request_id = "turn-2:api:1"
            _initial(f)
            payload = json.loads(json.loads(f.result)["result"])
            payload["reply"] = "Invented external completion."
            f.result = json.dumps({"result": json.dumps(payload)})
            f.args = {"request_id": f.request_id}
            read_name = TOOL.removesuffix("submit") + "read"
            content = _actual_dispatch_and_commit(f, monkeypatch, name=read_name, call_id="call-read")
            f.agent._current_api_request_id = "turn-2:api:2"
            read = _frames(protocol, f.args, content, name=read_name, call_id="call-read")
            history_key = "input" if protocol == "responses" else "messages"
            read[history_key] = submit[history_key] + read[history_key]
            wire, authorization = authorize_agent_sdk_kwargs(f.agent, read)
            assert wire == read and authorization.allowed, json.dumps({"wire": wire, "expected": read})
            f.args = {"request_id": f.request_id, "event_id": payload["event_id"], "reply_sha256": payload["reply_sha256"]}
            f.result = json.dumps({"result": json.dumps({"acknowledged": True})})
            ack_name = TOOL.removesuffix("submit") + "ack"
            from agent.native_metadata_capture import _expected_operation, _current_owner, captured_native_metadata
            assert _expected_operation(_current_owner(f.agent), f.agent, ack_name, f.args, f.result) is not None
            content = _actual_dispatch_and_commit(f, monkeypatch, name=ack_name, call_id="call-ack")
            ack_record, = [record for record in captured_native_metadata(f.agent) if record.call_id == "call-ack"]
            assert ack_record.presentation_sha256

            f.agent._current_api_request_id = "turn-2:api:3"
            ack = _frames(protocol, f.args, content, name=ack_name, call_id="call-ack")
            ack[history_key] = read[history_key] + ack[history_key]
            wire, authorization = authorize_agent_sdk_kwargs(f.agent, ack)
            assert wire == ack and authorization.allowed
            f.agent = SimpleNamespace(**{key: value for key, value in vars(f.agent).items()
                if not key.startswith("_native_metadata")})
            f.agent._current_turn_id = "turn-3"
            f.agent._current_api_request_id = "turn-3:api:1"
            continuation = {history_key: ack[history_key] + [{"role": "user", "content": "Continue the existing conversation."}]}
            wire, authorization = authorize_agent_sdk_kwargs(f.agent, continuation)
            assert wire == continuation and authorization.allowed


@pytest.mark.parametrize("mutation", ["source", "policy", "entry", "request", "recipient"])
def test_owner_binding_is_rechecked_at_actual_firewall_render(tmp_path, monkeypatch, mutation):
    from agent.native_metadata_capture import native_metadata_execution_scope
    from agent.llm_egress_firewall import LLMEgressFirewall
    with _owner() as f:
        _configure(f, tmp_path)
        route = SimpleNamespace(provider=f.agent.provider, model=f.agent.model, base_url=f.agent.base_url, api_mode=f.agent.api_mode)
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            content = _actual_dispatch_and_commit(f, monkeypatch)
            f.agent._current_api_request_id = "turn-1:api:2"
            original = LLMEgressFirewall._construct_typed_request
            def race(firewall, *args, **kwargs):
                mutations = {
                    "source": lambda: setattr(f.authority.sessions[f.source.chat_id], "source", SimpleNamespace(**vars(f.source))),
                    "policy": lambda: setattr(f.policy, "source", "cli"),
                    "entry": lambda: _replace_entry(f),
                    "request": lambda: setattr(f.agent, "_current_api_request_id", "turn-1:api:3"),
                    "recipient": lambda: setattr(route, "model", "changed-after-reference"),
                }
                mutations[mutation]()
                return original(firewall, *args, **kwargs)
            monkeypatch.setattr(LLMEgressFirewall, "_construct_typed_request", race)
            with pytest.raises(EgressBlocked) as error:
                authorize_agent_sdk_kwargs(f.agent, _frames("chat", f.args, content), route=route)
            assert "invalid_native_metadata_reference" in error.value.decision.reason_codes


@pytest.mark.parametrize("shape", ["extra-positive", "shadowed", "null-override", "malformed-override"])
def test_effective_sdk_input_is_the_only_native_pairing_source(tmp_path, monkeypatch, shape):
    from agent.native_metadata_capture import native_metadata_execution_scope
    with _owner() as f:
        _configure(f, tmp_path, "responses")
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            content = _actual_dispatch_and_commit(f, monkeypatch)
            f.agent._current_api_request_id = "turn-1:api:2"
            frames = _frames("responses", f.args, content)["input"]
            if shape == "extra-positive":
                body = {"extra_body": {"input": frames}}
                wire, authorization = authorize_agent_sdk_kwargs(f.agent, body)
                assert wire == body and authorization.allowed
            else:
                override = [frames[-1]] if shape == "shadowed" else None if shape == "null-override" else {"nested": frames}
                body = {"input": frames, "extra_body": {"input": override}}
                with pytest.raises(EgressBlocked):
                    authorize_agent_sdk_kwargs(f.agent, body)


@pytest.mark.parametrize("body", [{"messages": None}, {"input": None},
    {"messages": [{"role": "assistant", "tool_calls": None, "content": None}]},
    {"messages": "not-frames", "extra_body": {"input": None}}])
def test_optional_proof_declines_malformed_collections_without_type_error(tmp_path, body):
    from agent.native_metadata_capture import native_metadata_execution_scope
    with _owner() as f:
        _configure(f, tmp_path)
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            try:
                authorize_agent_sdk_kwargs(f.agent, body)
            except EgressBlocked:
                pass


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("bad", [None, "unadmitted", "cold", "partial", "new-id", "nested", "duplicate", "prose", "terminal-base64", "extra-base64", "recipient"])
def test_later_context_reuses_only_exact_previously_admitted_pair(tmp_path, monkeypatch, protocol, bad):
    from agent.native_metadata_capture import native_metadata_execution_scope
    from agent.native_metadata_egress import _COMPLETION_CONTEXT
    with _owner() as f:
        _configure(f, tmp_path, protocol)
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            content = _actual_dispatch_and_commit(f, monkeypatch)
            f.agent._current_api_request_id = "turn-1:api:2"
            if bad != "unadmitted":
                authorize_agent_sdk_kwargs(f.agent, _frames(protocol, f.args, content))
            payload = json.loads(json.loads(f.result)["result"])
            event = {"request_id": f.request_id, "completion_event_id": payload["event_id"],
                     "event_id": "terminal_" + uuid.uuid5(uuid.NAMESPACE_URL, "invented-scope:" + f.request_id).hex,
                     "packet_sha256": "d" * 64, "reply_sha256": payload["reply_sha256"],
                     "status": "completed", "work_item_id": "work-1"}
            if bad == "cold":
                f.authority.sessions[f.source.chat_id] = SimpleNamespace(source=f.source)
            mutations = {
                "partial": lambda value: {key: item for key, item in value.items() if key != "completion_event_id"},
                "new-id": lambda value: {**value, "request_id": "15d3b951-c055-5a48-ae33-98667401fe91"},
                "nested": lambda value: {"nested": value},
                "terminal-base64": lambda value: {**value, "event_id": "U3ludGhldGljIGV4dGVybmFsIHBheWxvYWQ="},
                "extra-base64": lambda value: {**value, "external": "U3ludGhldGljIGV4dGVybmFsIHBheWxvYWQ="},
            }
            event = mutations.get(bad, lambda value: value)(event)
            text = _COMPLETION_CONTEXT + json.dumps(event, sort_keys=True, separators=(",", ":"))
            if bad == "duplicate":
                text = text[:-1] + ',"request_id":"' + f.request_id + '"}'
            elif bad == "prose":
                text = "Copied arbitrary prose: " + text
            # Actual same-owner next turn and new agent; old API stamps/aliases
            # are absent, and context carries no accepting metadata sidecar.
            f.agent = SimpleNamespace(**{key: value for key, value in vars(f.agent).items()
                if not key.startswith("_native_metadata")})
            f.agent._current_turn_id = "turn-2"
            f.agent._current_api_request_id = "turn-2:api:1"
            if bad == "recipient":
                f.agent.model = "different-recipient-model"
            if protocol == "responses":
                body = {"input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}]}
            elif protocol == "anthropic":
                body = {"messages": [{"role": "user", "content": [{"type": "text", "text": text}]}]}
            else:
                body = {"messages": [{"role": "user", "content": text}]}
            if bad is None:
                wire, authorization = authorize_agent_sdk_kwargs(f.agent, body)
                assert wire == body and authorization.allowed
                assert authorization.decision.source_segment_count == 0
            else:
                with pytest.raises(EgressBlocked):
                    authorize_agent_sdk_kwargs(f.agent, body)


def test_actual_execution_cannot_acquire_a_changed_recipient_observation(tmp_path, monkeypatch):
    from agent.native_metadata_capture import native_metadata_execution_scope, captured_native_metadata
    with _owner() as f:
        _configure(f, tmp_path)
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            calls = []
            def execute(_arguments):
                calls.append(1)
                route = SimpleNamespace(provider=f.agent.provider, model="changed-during-execution",
                    base_url=f.agent.base_url, api_mode=f.agent.api_mode)
                authorize_agent_sdk_kwargs(f.agent, {"messages": [{"role": "user", "content": "Invented context."}]}, route=route)
                return f.result
            content = _actual_dispatch_and_commit(f, monkeypatch, execute=execute)
            assert calls == [1] and captured_native_metadata(f.agent) == ()
            f.agent._current_api_request_id = "turn-1:api:2"
            with pytest.raises(EgressBlocked):
                authorize_agent_sdk_kwargs(f.agent, _frames("chat", f.args, content))


def test_unexecuted_returned_metadata_cannot_construct_authority(tmp_path):
    from agent.native_metadata_capture import native_metadata_execution_scope
    from agent.tool_dispatch_helpers import _maybe_wrap_untrusted
    with _owner() as f:
        _configure(f, tmp_path)
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            f.agent._current_api_request_id = "turn-1:api:2"
            body = _frames("chat", f.args, _maybe_wrap_untrusted(TOOL, f.result))
            with pytest.raises(EgressBlocked):
                authorize_agent_sdk_kwargs(f.agent, body)


def test_constructed_reference_is_not_an_egress_capability(tmp_path):
    from agent.llm_egress_firewall import LLMEgressFirewall, TypedOutboundRequest
    from agent.native_metadata_egress import NativeMetadataSegment
    request = TypedOutboundRequest(payload={"text": NativeMetadataSegment("invented-value", object())},
        session_id="session", turn_id="turn", request_id="request", policy_digest="policy")
    route = SimpleNamespace(provider="nous", model="offline-model", base_url="https://inference-api.nousresearch.com/v1", api_mode="chat_completions")
    with pytest.raises(EgressBlocked) as error:
        LLMEgressFirewall(tmp_path, policy_digest="policy").authorize(request, route)
    assert "invalid_native_metadata_reference" in error.value.decision.reason_codes


def test_more_than_sixty_four_actual_calls_keep_full_history_and_known_pair(tmp_path, monkeypatch):
    from agent.native_metadata_capture import native_metadata_execution_scope
    from agent.native_metadata_derivation import expected_submit_ids
    from agent.native_metadata_egress import _COMPLETION_CONTEXT
    with _owner() as f:
        _configure(f, tmp_path)
        template_args, template_payload = f.args, json.loads(json.loads(f.result)["result"])
        history, first = [], None
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            for number in range(66):
                f.agent._current_api_request_id = f"turn-1:api:{number * 2 + 1}"
                _initial(f)
                f.args = json.loads(json.dumps(template_args))
                f.args["task"]["work_item_id"] = f"work-{number}"
                expected = expected_submit_ids(logical_session_id=f.source.chat_id,
                    target="claude", work_item_id=f.args["task"]["work_item_id"])
                first = first or expected
                payload = {**template_payload, "request_id": expected.request_id, "event_id": expected.event_id}
                f.result = json.dumps({"result": json.dumps(payload)})
                call_id = f"call-{number}"
                content = _actual_dispatch_and_commit(f, monkeypatch, call_id=call_id)
                f.agent._current_api_request_id = f"turn-1:api:{number * 2 + 2}"
                body = _frames("chat", f.args, content, call_id=call_id)
                wire, authorization = authorize_agent_sdk_kwargs(f.agent, body)
                assert wire == body and authorization.allowed
                history.extend(body["messages"])
            f.agent._current_turn_id = "turn-2"
            f.agent._current_api_request_id = "turn-2:api:1"
            text = _COMPLETION_CONTEXT + json.dumps({"request_id": first.request_id,
                "completion_event_id": first.event_id, "status": "completed"})
            body = {"messages": history + [{"role": "user", "content": text}]}
            wire, authorization = authorize_agent_sdk_kwargs(f.agent, body)
            assert wire == body and authorization.allowed


@pytest.mark.parametrize("segment_cap", [32768, 131072])
def test_existing_configured_policy_allows_large_presentations_without_hidden_cap(tmp_path, monkeypatch, segment_cap):
    from agent.native_metadata_capture import native_metadata_execution_scope
    with _owner() as f:
        _configure(f, tmp_path)
        f.agent._llm_egress_max_sanitized_segment_bytes = segment_cap
        payload = json.loads(json.loads(f.result)["result"])
        payload["external"] = "word. " * 12000
        f.result = json.dumps({"result": json.dumps(payload)})
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            from tools.budget_config import BudgetConfig
            content = _actual_dispatch_and_commit(f, monkeypatch,
                budget=BudgetConfig(default_result_size=131072, mcp_result_size=131072))
            f.agent._current_api_request_id = "turn-1:api:2"
            body = _frames("chat", f.args, content)
            wire, authorization = authorize_agent_sdk_kwargs(f.agent, body)
            assert wire == body and authorization.allowed


def test_proven_identifier_can_cross_existing_chunk_boundary_without_wire_change(tmp_path, monkeypatch):
    from agent.native_metadata_capture import native_metadata_execution_scope
    from agent.tool_dispatch_helpers import _maybe_wrap_untrusted
    with _owner() as f:
        _configure(f, tmp_path)
        full = _maybe_wrap_untrusted(TOOL, f.result)
        f.agent._llm_egress_max_sanitized_segment_bytes = full.encode().index(f.request_id.encode()) + len(f.request_id) // 2
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            content = _actual_dispatch_and_commit(f, monkeypatch)
            f.agent._current_api_request_id = "turn-1:api:2"
            body = _frames("chat", f.args, content)
            wire, authorization = authorize_agent_sdk_kwargs(f.agent, body)
            assert wire == body and authorization.allowed


@pytest.mark.parametrize(("kind", "value", "reason"), [
    ("encoding", "U3ludGhldGljIGV4dGVybmFsIHBheWxvYWQ=", "base64_payload"),
    ("secret", "explicit-synthetic-sensitive-value", "exact_secret_detected"),
    ("path", "/Users/synthetic/private-file", "private_absolute_path"),
    ("source", "owned-source-material", "source_bytes_in_sanitized_segment"),
])
def test_external_content_guards_rejoin_original_native_chunks(tmp_path, monkeypatch, kind, value, reason):
    from agent.native_metadata_capture import native_metadata_execution_scope
    from agent.tool_dispatch_helpers import _maybe_wrap_untrusted
    from agent.llm_egress_firewall import LLMEgressFirewall
    with _owner() as f:
        _configure(f, tmp_path)
        payload = json.loads(json.loads(f.result)["result"])
        payload["external"] = value
        provisional = json.dumps({"result": json.dumps(payload)})
        offset = _maybe_wrap_untrusted(TOOL, provisional).encode().index(value.encode())
        padding = 32768 - offset - len(value.encode()) // 2
        payload["external"] = "word. " * (padding // 6) + " " * (padding % 6) + value + " harmless. " * 100
        f.result = json.dumps({"result": json.dumps(payload)})
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _initial(f)
            content = _actual_dispatch_and_commit(f, monkeypatch)
            f.agent._current_api_request_id = "turn-1:api:2"
            if kind == "secret":
                monkeypatch.setattr("agent.llm_egress_runtime._exact_provider_secret_values", lambda: (value,))
            if kind == "source":
                original = LLMEgressFirewall._construct_typed_request
                def verified_contents(firewall, request, _grants, **kwargs):
                    # Unit boundary after existing grant verification: inject
                    # synthetic verified contents, not a SourceGrant/authority.
                    return original(firewall, request, {"synthetic": (None, value.encode())}, **kwargs)
                monkeypatch.setattr(LLMEgressFirewall, "_construct_typed_request", verified_contents)
            with pytest.raises(EgressBlocked) as error:
                authorize_agent_sdk_kwargs(f.agent, _frames("chat", f.args, content))
            assert reason in error.value.decision.reason_codes
