"""Actual dispatch captures derived IDs; no external content becomes trusted."""
from contextlib import contextmanager
from hashlib import sha256
import importlib
import json
from types import SimpleNamespace
import uuid

import pytest

from gateway.config import Platform
from gateway.session_local import LocalSessionAdapter
from gateway.session_local_mcp import session_scope
from hermes_constants import hermes_home_key
from tools.registry import registry, session_tool_scope


PREFIX = "mcp__editor_native_capture_fixture__"
NAMES = tuple(PREFIX + operation for operation in ("a2a_policy", "a2a_submit", "a2a_read", "a2a_ack"))
TOOL = PREFIX + "a2a_submit"


def _module():
    try:
        module = importlib.import_module("agent.native_metadata_capture")
    except ModuleNotFoundError:
        module = None
    assert module is not None, "private owner-bound actual-dispatch capture is not implemented"
    return module


@contextmanager
def _owner(*, logical="local-invented", durable="durable-invented", source_kind="acp"):
    authority = SimpleNamespace(profile_id=hermes_home_key(), sessions={})
    source = SimpleNamespace(platform=Platform.LOCAL, chat_id=logical, user_id="owner",
                             profile="", chat_type="dm")
    authority.sessions[logical] = SimpleNamespace(source=source)
    adapter = LocalSessionAdapter(authority)
    adapter.register_source(source)
    scope = session_scope(authority, logical)
    toolset = "mcp-editor_native_capture_fixture"
    data = {"scope": scope, "schemas": {name: {"name": name} for name in NAMES},
            "names": {"0": "editor_native_capture_fixture"}}
    policy = SimpleNamespace(source=source_kind, toolsets=(toolset,),
        request_json=json.dumps({"toolsets": []}), editor_mcp_json=json.dumps(data))
    adapter.policies[logical] = policy
    args = {"target": "claude", "task": {"work_item_id": "work-1", "project_id": "project",
        "task_class": "question", "commit": "a" * 40, "question": "Explain this.",
        "sources": [], "evidence": []}}
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
        ["hermes-" + logical, "hermes", "claude", "work-1"],
        sort_keys=True, separators=(",", ":"), allow_nan=False)))
    result = json.dumps({"result": json.dumps({"request_id": request_id,
        "event_id": "evt_" + uuid.uuid5(uuid.NAMESPACE_URL, "mira-claude:" + request_id).hex,
        "reply_sha256": sha256(b"invented completion").hexdigest(), "state": "awaiting_ack",
        "target": "claude", "origin": "hermes"}, separators=(",", ":"))})
    for name in NAMES:
        registry.register(name, toolset, {"name": name}, lambda _args: None, scope=scope)
    agent = SimpleNamespace(session_id=durable, _current_turn_id="turn-1",
        _current_api_request_id="turn-1:api:1", _llm_egress_policy_digest=sha256(b"policy").hexdigest())
    try:
        with session_tool_scope(scope):
            yield SimpleNamespace(authority=authority, policy=policy, source=source,
                agent=agent, args=args, result=result, scope=scope, request_id=request_id)
    finally:
        for name in NAMES:
            registry.deregister(name, scope=scope)


def _capture(module, fixture, execute=None, *, arguments=None):
    arguments = fixture.args if arguments is None else arguments
    return module.execute_with_native_metadata_capture(fixture.agent, TOOL, "call-1",
        module.argument_digest(fixture.args), execute or (lambda _args: fixture.result), arguments)


def test_owner_bound_dispatch_captures_only_independently_derived_ids():
    module = _module()
    with _owner() as f:
        with module.native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            assert _capture(module, f) == f.result
        records = module.captured_native_metadata(f.agent)
        assert len(records) == 1
        record = records[0]
        assert record.logical_session_id == "local-invented"
        assert record.durable_session_id == "durable-invented"
        assert record.request_id == f.request_id
        assert record.result_sha256 == sha256(f.result.encode()).hexdigest()
        assert not hasattr(record, "reply") and not hasattr(record, "source_grant")


@pytest.mark.parametrize("bad", ["unbound", "foreign-source", "ordinary", "wrong-overlay",
    "wrong-agent", "rewritten-args", "wrong-result", "duplicate-json", "entry-replaced", "exception"])
def test_unverified_inputs_never_issue_a_capture(bad):
    module = _module()
    with _owner(source_kind="cli" if bad == "ordinary" else "acp") as f:
        if bad == "foreign-source":
            f.source = SimpleNamespace(**vars(f.source))
        if bad == "wrong-agent":
            f.agent.session_id = "foreign-durable"
        if bad == "wrong-result":
            f.result = f.result.replace(f.request_id, str(uuid.uuid4()))
        if bad == "duplicate-json":
            f.result = '{"result":"x","result":"y"}'
        calls = []
        def execute(_args):
            calls.append(1)
            if bad == "entry-replaced":
                registry.deregister(TOOL, scope=f.scope)
                registry.register(TOOL, "mcp-editor_native_capture_fixture", {"name": TOOL},
                                  lambda _args: None, scope=f.scope)
            if bad == "exception":
                raise ValueError("invented dispatch error")
            return f.result
        with module.native_metadata_execution_scope(f.authority, f.policy, f.source, "durable-invented"):
            with session_tool_scope(None if bad in {"unbound", "wrong-overlay"} else f.scope):
                if bad == "exception":
                    with pytest.raises(ValueError, match="invented dispatch error"):
                        _capture(module, f, execute)
                else:
                    args = dict(f.args, target="codex") if bad == "rewritten-args" else f.args
                    assert _capture(module, f, execute, arguments=args) == f.result
        assert calls == [1]  # Observation never suppresses or replays actual dispatch.
        assert module.captured_native_metadata(f.agent) == ()


def test_nested_ordinary_owner_scope_clears_and_restores_binding():
    module = _module()
    with _owner() as f:
        with module.native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            with module.native_metadata_execution_scope(None, None, None, ""):
                _capture(module, f)
                assert module.captured_native_metadata(f.agent) == ()
            _capture(module, f)
        assert len(module.captured_native_metadata(f.agent)) == 1


@pytest.mark.parametrize("bad", ["none", "blocked", "error", "uncommitted", "changed-presentation"])
def test_final_presentation_and_next_request_are_bound_separately(bad):
    from agent.tool_dispatch_helpers import _maybe_wrap_untrusted
    module = _module()
    with _owner() as f:
        with module.native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _capture(module, f)
            record, = module.captured_native_metadata(f.agent)
            assert record.execution_api_request_id == "turn-1:api:1"
            assert record.presentation_sha256 is None and record.next_provider_request_id is None
            module.commit_native_metadata_presentation(f.agent, "call-1", f.result,
                f.result + "changed" if bad == "changed-presentation" else _maybe_wrap_untrusted(TOOL, f.result),
                blocked=bad == "blocked", is_error=bad == "error", committed=bad != "uncommitted")
        records = module.captured_native_metadata(f.agent)
        if bad == "none":
            assert records[0].next_provider_request_id == "turn-1:api:2"
            assert records[0].presentation_sha256 == sha256(_maybe_wrap_untrusted(TOOL, f.result).encode()).hexdigest()
        else:
            assert records == ()


def test_same_call_duplicate_is_a_tombstone_not_an_overwrite():
    module = _module()
    with _owner() as f:
        with module.native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _capture(module, f)
            _capture(module, f)
            assert module.captured_native_metadata(f.agent) == ()


@pytest.mark.parametrize("after", ["unchanged", "rewrite", "block"])
def test_actual_owner_turn_and_threaded_dispatch_reject_post_execute_changes(monkeypatch, after):
    from concurrent.futures import ThreadPoolExecutor
    from agent import tool_executor
    from gateway.run_turn_runner import TurnRunner
    from tests.agent.test_tool_activity_heartbeat import _make_agent
    from tools.thread_context import propagate_context_to_thread

    module = _module()
    with _owner() as f:
        agent = _make_agent(monkeypatch)
        for key, value in vars(f.agent).items():
            setattr(agent, key, value)
        f.agent = agent
        agent._tool_guardrails = SimpleNamespace(before_call=lambda *_a: SimpleNamespace(allows_execution=True))
        monkeypatch.setattr("hermes_cli.plugins._dispatch_pre_tool_call_hooks", lambda *_a, **_kw: (None, None))
        # Transport acquisition is independently tested by existing MCP owner
        # tests. This inert boundary keeps its overlay, without connecting MCP.
        @contextmanager
        def frozen_scope(_policy, *, authority):
            assert authority is f.authority
            with session_tool_scope(f.scope):
                yield
        monkeypatch.setattr("gateway.session_policy.policy_scope", frozen_scope)
        monkeypatch.setattr("gateway.session_authorities.active_authority", lambda _runner: f.authority)
        runner = SimpleNamespace(_adapter_for_source=lambda _source: f.source._transport_adapter_ref())
        turn = TurnRunner.__new__(TurnRunner)
        turn._ctx = SimpleNamespace(source=f.source, session_id=agent.session_id)
        turn._runner = runner
        monkeypatch.setattr("gateway.session_policy.bind_turn_workspace", lambda *_a: None)

        def middleware(_name, args, callback, **_kwargs):
            result = callback(args)
            if after == "block":
                return '{"error":"post-dispatch block"}'
            return result + "changed" if after == "rewrite" else result
        monkeypatch.setattr("hermes_cli.middleware.run_tool_execution_middleware", middleware)
        monkeypatch.setattr("hermes_cli.middleware.apply_tool_request_middleware",
            lambda _name, args, **_kw: SimpleNamespace(payload=args, trace=[]))
        calls = []
        def execute(args):
            calls.append(args)
            return f.result
        def scoped_turn():
            with ThreadPoolExecutor(max_workers=1) as pool:
                outcome = pool.submit(propagate_context_to_thread(lambda:
                    tool_executor._run_agent_tool_execution_middleware(agent,
                        function_name=TOOL, function_args=f.args, effective_task_id="task",
                        tool_call_id="call-1", execute=execute))).result(timeout=10)
            return outcome.result
        turn._run_sync_scoped = scoped_turn
        result = turn.run_sync()
        assert calls == [f.args]
        records = module.captured_native_metadata(agent)
        assert bool(records) == (after == "unchanged")
        assert result == (f.result if after == "unchanged" else
            '{"error":"post-dispatch block"}' if after == "block" else f.result + "changed")


@pytest.mark.parametrize("bad", ["none", "blocked", "error", "flush-failed", "presentation-rewritten"])
def test_actual_commit_path_requires_successful_unchanged_final_presentation(monkeypatch, bad):
    from agent import tool_executor
    from tests.agent.test_tool_activity_heartbeat import _make_agent
    from tools.budget_config import DEFAULT_BUDGET

    module = _module()
    with _owner() as f:
        agent = _make_agent(monkeypatch)
        for key, value in vars(f.agent).items():
            setattr(agent, key, value)
        f.agent = agent
        if bad == "flush-failed":
            agent._flush_messages_to_session_db = lambda *_a, **_kw: False
        if bad == "presentation-rewritten":
            agent._tool_result_content_for_active_model = lambda _name, result: result + "changed"
        with module.native_metadata_execution_scope(f.authority, f.policy, f.source, agent.session_id):
            _capture(module, f)
            messages = []
            result = tool_executor._commit_tool_result(agent, messages,
                tool_executor._ToolCallRef(TOOL, f.args, "task", "call-1", []), f.result,
                budget=DEFAULT_BUDGET, tool_duration=0.0, is_error=bad == "error",
                blocked=bad == "blocked", effect_disposition="none")
        assert (result is None) == (bad == "flush-failed")
        records = module.captured_native_metadata(agent)
        assert bool(records) == (bad == "none")
        if records:
            assert records[0].next_provider_request_id == "turn-1:api:2"
            assert records[0].presentation_sha256 == sha256(messages[-1]["content"].encode()).hexdigest()
            assert records[0].presentation_sha256 != records[0].result_sha256
            assert messages[-1]["content"].startswith('<untrusted_tool_result source="')


@pytest.mark.parametrize("changed", ["_current_turn_id", "_current_api_request_id", "_llm_egress_policy_digest"])
def test_identity_changes_during_dispatch_or_before_commit_decline_observation(changed):
    module = _module()
    with _owner() as f:
        with module.native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            def changed_execute(_args):
                setattr(f.agent, changed, "changed")
                return f.result
            _capture(module, f, changed_execute)
            assert module.captured_native_metadata(f.agent) == ()


@pytest.mark.parametrize("when", ["before", "after"])
def test_observer_failure_never_replaces_actual_result_or_exception(monkeypatch, when):
    module = _module()
    with _owner() as f:
        with module.native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            actual = module._current_owner
            calls = []
            def broken(agent):
                calls.append(1)
                if (when == "before" and len(calls) == 1) or (when == "after" and len(calls) == 2):
                    raise RuntimeError("observer failed")
                return actual(agent)
            monkeypatch.setattr(module, "_current_owner", broken)
            executed = []
            assert _capture(module, f, lambda _args: executed.append(1) or f.result) == f.result
            assert executed == [1]
            sentinel = ValueError("actual dispatch exception")
            def fail(_args):
                raise sentinel
            with pytest.raises(ValueError) as caught:
                _capture(module, f, fail)
            assert caught.value is sentinel


def test_ordinary_tool_arguments_are_not_serialized_for_observation(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "argument_digest", lambda _args: pytest.fail("ordinary arguments serialized"))
    assert module.native_metadata_argument_digest(SimpleNamespace(), "ordinary", object()) is None


def test_same_owner_agent_rebuild_retains_observation_but_cold_owner_does_not():
    module = _module()
    with _owner() as f:
        with module.native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            _capture(module, f)
            rebuilt = SimpleNamespace(**vars(f.agent))
            del rebuilt._native_metadata_captures
            rebuilt._current_turn_id = "turn-2"
            rebuilt._current_api_request_id = "turn-2:api:1"
            assert len(module.captured_native_metadata(rebuilt)) == 1
            del f.authority.sessions[f.source.chat_id]._native_metadata_captures
            assert module.captured_native_metadata(rebuilt) == ()
