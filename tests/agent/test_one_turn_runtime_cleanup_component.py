"""Unexecuted offline components; root must review containment before running.

The real_components fixture installs isolation/fences before production imports.
No full cli, run_agent or TUI server is imported. A narrowly declared cli logger
facade serves only the existing lazy logging import in the real primary helper.
The real voice recovery runs with its runtime installer replaced by an offline
callback; no client factory, credential resolver or provider is exercised. These
cases do not qualify complete CLI chat dispatch or server-global rebinding.
"""

import importlib
import contextlib
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from tests.agent.startup_runtime_component_support import SOURCE, EffectDenied, real_components

# Explicitly export imported fixtures used by pytest name-based injection.
__all__ = ['real_components']


@pytest.fixture
def components(real_components, monkeypatch):
    names = (
        "hermes_cli.cli_model_switch_mixin", "hermes_cli.cli_chat_turn_mixin",
        "tui_gateway.model_switch", "tui_gateway.prompt_turn", "agent.voice_turn_route",
        "agent.runtime_mutation", "agent.route_binding",
    )
    modules = [importlib.import_module(name) for name in names]
    for module in modules:
        assert Path(module.__file__).resolve().is_relative_to(SOURCE)
    assert "cli" not in sys.modules
    assert "run_agent" not in sys.modules and "tui_gateway.server" not in sys.modules
    for module in (modules[2], modules[3]):
        monkeypatch.setattr(module, "logger", logging.getLogger(__name__), raising=False)
    return SimpleNamespace(
        cli=modules[0], chat=modules[1], tui=modules[2], prompt=modules[3],
        voice=modules[4], mutation=modules[5], binding=modules[6], snapshot=real_components[1])


@pytest.fixture
def logger_only_cli_facade(components, monkeypatch):
    # Explicit component seam, not a bootstrap/agent replacement or CLI startup.
    facade = ModuleType("cli")
    facade.logger = logging.getLogger(__name__)
    monkeypatch.setitem(sys.modules, "cli", facade)
    return facade


def _unexpected(*args, **kwargs):
    raise EffectDenied("unexpected component side effect or fallback switch")


@pytest.mark.parametrize("owner", ["cli", "tui"])
@pytest.mark.parametrize("outcome", ["busy", "pending", "stale"])
@pytest.mark.parametrize("has_newer", [False, True])
def test_owning_cleanup_retains_only_current_obligation(
    components, monkeypatch, owner, outcome, has_newer,
):
    snapshot, newer, original_error = {"old": True}, {"new": True}, ValueError("turn failed")
    scheduled = {"unused-once": True}
    original_result = {"failed": True, "error": original_error}
    errors = {
        "busy": components.mutation.RuntimeMutationBusyError,
        "pending": components.snapshot.RuntimeRecoveryPendingError,
        "stale": components.mutation.RuntimeMutationStaleError,
    }

    def restore(*args):
        raise errors[outcome]("synthetic local boundary")

    if owner == "cli":
        state = SimpleNamespace(_restore_model_runtime_snapshot=restore, result=original_result,
                                _pending_one_turn_model_restore=scheduled,
                                _one_turn_runtime_cleanup=newer if has_newer else None)
        components.chat._restore_one_turn_after_chat(state, snapshot)
        retained = state._one_turn_runtime_cleanup
        assert state._pending_one_turn_model_restore is scheduled
        assert state.result is original_result and state.result["error"] is original_error
    else:
        state = {"result": original_result, "one_turn_model_restore": scheduled}
        if has_newer:
            state["one_turn_runtime_cleanup"] = newer
        monkeypatch.setattr(components.prompt, "_restore_agent_model_runtime", restore, raising=False)
        for name in ("_restart_slash_worker", "_persist_live_session_runtime", "_persist_live_session_system_prompt"):
            monkeypatch.setattr(components.prompt, name, _unexpected, raising=False)
        components.prompt._restore_one_turn_after_prompt("synthetic", state, SimpleNamespace(), snapshot)
        retained = state.get("one_turn_runtime_cleanup")
        assert state["one_turn_model_restore"] is scheduled
        assert state["result"] is original_result and state["result"]["error"] is original_error
    expected = newer if has_newer else (None if outcome == "stale" else snapshot)
    assert retained is expected


def test_actual_tui_release_continues_scope_cleanup_after_busy_restore(components, monkeypatch):
    snapshot, result = {"old": True}, {"error": "original"}
    seen = []

    def busy(*args):
        raise components.mutation.RuntimeMutationBusyError("synthetic busy")

    monkeypatch.setattr(components.prompt, "_restore_agent_model_runtime", busy, raising=False)
    monkeypatch.setattr(components.prompt, "contextlib", importlib.import_module("contextlib"), raising=False)
    monkeypatch.setattr(components.prompt, "reset_secret_scope", lambda token: seen.append(("secret", token)), raising=False)
    monkeypatch.setattr(components.prompt, "_clear_session_context", lambda tokens: seen.append(("session", tokens)), raising=False)
    for name in ("_restart_slash_worker", "_persist_live_session_runtime", "_persist_live_session_system_prompt"):
        monkeypatch.setattr(components.prompt, name, _unexpected, raising=False)
    scopes = components.prompt._TurnScopes(secret="synthetic-token", session_tokens=["synthetic-context"])
    turn = components.prompt._TurnRun(SimpleNamespace(), snapshot, None, False, scopes=scopes, result=result)
    turn.history, turn.run_kwargs = [{"synthetic": True}], {"synthetic": True}
    session = {}
    components.prompt._release_turn_scopes("synthetic", session, turn)
    assert session["one_turn_runtime_cleanup"] is snapshot and turn.result is result
    assert "one_turn_model_restore" not in session
    assert turn.history == [] and turn.run_kwargs == {}
    assert seen == [("secret", "synthetic-token"), ("session", ["synthetic-context"])]


def _voice_debt(components, agent, *, debt=True):
    state = {"reasoning_config": {"effort": "before-voice"}, "snapshot": {"model": "saved"}}
    state.update({name: getattr(agent, name, None) for name in components.voice._EXTRA_FIELDS})
    agent._voice_route_state, agent._turn_route_task = state, "voice_chat"
    with components.mutation.runtime_mutation(agent, "begin_voice_turn_route") as lease:
        record = components.voice._register_voice(agent, state, lease)
        if debt:
            lease.controller.defer_voice(record)
    return record


def _agent():
    return SimpleNamespace(
        _primary_runtime={"model": "original-preferred"}, model="active-voice",
        reasoning_config={"effort": "voice"}, _fallback_activated=True,
        _rate_limited_until=17, switch_model=_unexpected, _restore_primary_runtime=_unexpected,
    )


@pytest.mark.parametrize("adapter", ["cli", "tui"])
def test_actual_adapter_failed_voice_recovery_precedes_every_restore_write(components, monkeypatch, adapter):
    agent = _agent()
    original, reasoning = agent._primary_runtime, agent.reasoning_config
    record = _voice_debt(components, agent)

    def failed_install(target, runtime):
        assert target._primary_runtime is original
        raise ValueError("synthetic installer failure")

    monkeypatch.setattr(components.binding, "reinstall_runtime_snapshot", failed_install)
    snapshot = {"model": "restore-target", "reasoning_config": {"effort": "restore"}}
    if adapter == "cli":
        snapshot["agent_primary_runtime"] = {"model": "saved-preferred"}
        surface = SimpleNamespace(agent=agent, model="surface-original", reasoning_config=reasoning)
        restore = lambda: components.cli.CLIModelSwitchMixin._restore_model_runtime_snapshot(surface, snapshot)
    else:
        snapshot["primary_runtime"] = {"model": "saved-preferred"}
        restore = lambda: components.tui._restore_agent_model_runtime(agent, snapshot)
    with pytest.raises(components.snapshot.RuntimeRecoveryPendingError):
        restore()
    assert agent._primary_runtime is original and agent.reasoning_config is reasoning
    assert agent.model == "active-voice" and agent._rate_limited_until == 17
    assert components.mutation.runtime_controller(agent).voice_snapshot()[:2] == (record, record)
    if adapter == "cli":
        assert surface.model == "surface-original" and surface.reasoning_config is reasoning
    assert "cli" not in sys.modules and "run_agent" not in sys.modules


@pytest.mark.parametrize("adapter", ["cli", "tui"])
def test_active_voice_without_debt_refuses_without_requesting_cleanup(components, adapter):
    agent = _agent()
    record = _voice_debt(components, agent, debt=False)
    controller = components.mutation.runtime_controller(agent)
    before = (agent._primary_runtime, agent.reasoning_config)
    module = components.cli if adapter == "cli" else components.tui
    with components.mutation.runtime_mutation(agent, "one_turn_restore") as lease:
        with pytest.raises(components.snapshot.RuntimeRecoveryPendingError):
            module._prepare_one_turn_runtime_restore(agent, lease)
    assert controller.voice_snapshot()[:2] == (record, None)
    assert not controller.cleanup_requested
    assert agent._primary_runtime is before[0] and agent.reasoning_config is before[1]


@pytest.mark.parametrize("adapter", ["cli", "tui"])
@pytest.mark.parametrize("primary_outcome", [False, "error"])
def test_voice_recovery_precedes_copy_and_failed_primary_preserves_identity(
    components, monkeypatch, logger_only_cli_facade, adapter, primary_outcome,
):
    agent = _agent()
    original = agent._primary_runtime
    record = _voice_debt(components, agent)
    seen = []

    def install(target, runtime):
        assert target._primary_runtime is original and runtime is record.state["snapshot"]
        seen.append("voice-restored-before-copy")

    monkeypatch.setattr(components.binding, "reinstall_runtime_snapshot", install)

    def primary_restore():
        with components.mutation.runtime_mutation(agent, "restore_primary_runtime"):
            assert seen == ["voice-restored-before-copy"]
            assert agent._primary_runtime is not original
            seen.append("primary-attempt")
            if primary_outcome == "error":
                raise ValueError("synthetic primary failure")
            return False

    agent._restore_primary_runtime = primary_restore
    snapshot = {"primary_runtime": {"model": "saved"}, "agent_primary_runtime": {"model": "saved"}}
    module = components.cli if adapter == "cli" else components.tui
    with components.mutation.runtime_mutation(agent, "one_turn_restore") as lease:
        module._prepare_one_turn_runtime_restore(agent, lease)
        assert components.mutation.runtime_controller(agent).voice_snapshot()[:2] == (None, None)
        if adapter == "cli":
            restored = module._try_one_turn_primary_restore(agent, snapshot, lease, _unexpected)
        else:
            restored = module._try_one_turn_primary_restore(agent, snapshot, lease)
        assert restored is False
        # This is the exact point where the adapter may subsequently try its fallback.
        assert agent._primary_runtime is original
        assert agent._fallback_activated is True and agent._rate_limited_until == 17
    assert seen == ["voice-restored-before-copy", "primary-attempt"]
    assert "run_agent" not in sys.modules and "tui_gateway.server" not in sys.modules


@pytest.mark.parametrize("primary_outcome", [False, "error"])
def test_actual_tui_fallback_observes_original_preferred_identity(components, primary_outcome):
    agent = _agent()
    original = agent._primary_runtime
    seen = []

    def primary_restore():
        with components.mutation.runtime_mutation(agent, "restore_primary_runtime"):
            if primary_outcome == "error":
                raise ValueError("synthetic primary failure")
            return False

    def fallback(**kwargs):
        with components.mutation.runtime_mutation(agent, "switch_model"):
            assert agent._primary_runtime is original
            seen.append("fallback-after-pointer-rollback")
            return False

    agent._restore_primary_runtime, agent.switch_model = primary_restore, fallback
    snapshot = {"primary_runtime": {"model": "saved-preferred"}, "model": "saved-active"}
    with pytest.raises(ValueError, match="restore declined"):
        components.tui._restore_agent_model_runtime(agent, snapshot)
    assert seen == ["fallback-after-pointer-rollback"]
    assert agent._primary_runtime is original


@pytest.mark.parametrize("owner", ["cli", "tui"])
@pytest.mark.parametrize("outcome", ["busy", "pending", "stale", "success"])
def test_preadmission_cleanup_blocks_or_consumes_only_completed_debt(components, monkeypatch, owner, outcome):
    debt, scheduled, result = {"completed": True}, {"unused": True}, {"error": "original"}
    seen = []
    errors = {"busy": components.mutation.RuntimeMutationBusyError,
              "pending": components.snapshot.RuntimeRecoveryPendingError,
              "stale": components.mutation.RuntimeMutationStaleError}

    def restore(*args):
        seen.append("restore")
        if outcome != "success":
            raise errors[outcome]("synthetic local boundary")

    if owner == "cli":
        state = SimpleNamespace(_one_turn_runtime_cleanup=debt, _pending_one_turn_model_restore=scheduled,
                                _restore_model_runtime_snapshot=restore, _last_turn_result=result)
        drain = lambda: components.chat._drain_one_turn_before_chat(state)
        retained = lambda: state._one_turn_runtime_cleanup
    else:
        state = {"one_turn_runtime_cleanup": debt, "one_turn_model_restore": scheduled,
                 "agent": SimpleNamespace(), "result": result}
        monkeypatch.setattr(components.prompt, "_restore_agent_model_runtime", restore, raising=False)
        for name in ("_restart_slash_worker", "_persist_live_session_runtime", "_persist_live_session_system_prompt"):
            monkeypatch.setattr(components.prompt, name, lambda *args: seen.append("publish"), raising=False)
        drain = lambda: components.prompt._drain_one_turn_before_prompt("synthetic", state)
        retained = lambda: state.get("one_turn_runtime_cleanup")
    if outcome in {"busy", "pending"}:
        with pytest.raises(components.snapshot.RuntimeRecoveryPendingError):
            drain()
        assert retained() is debt and seen == ["restore"]
    else:
        drain()
        assert retained() is None
        assert seen == (["restore", "publish", "publish", "publish"]
                        if owner == "tui" and outcome == "success" else ["restore"])
    if owner == "cli":
        assert state._pending_one_turn_model_restore is scheduled and state._last_turn_result is result
    else:
        assert state["one_turn_model_restore"] is scheduled and state["result"] is result


@pytest.mark.parametrize("owner", ["cli", "tui"])
def test_newer_cleanup_queued_during_drain_still_blocks_admission(components, monkeypatch, owner):
    old, newer, scheduled = {"old": True}, {"newer": True}, {"unused": True}
    if owner == "cli":
        state = SimpleNamespace(_one_turn_runtime_cleanup=old, _pending_one_turn_model_restore=scheduled)
        def restore(*args):
            state._one_turn_runtime_cleanup = newer
        state._restore_model_runtime_snapshot = restore
        drain = lambda: components.chat._drain_one_turn_before_chat(state)
    else:
        state = {"one_turn_runtime_cleanup": old, "one_turn_model_restore": scheduled, "agent": SimpleNamespace()}
        def restore(*args):
            state["one_turn_runtime_cleanup"] = newer
        monkeypatch.setattr(components.prompt, "_restore_agent_model_runtime", restore, raising=False)
        for name in ("_restart_slash_worker", "_persist_live_session_runtime", "_persist_live_session_system_prompt"):
            monkeypatch.setattr(components.prompt, name, lambda *args: None, raising=False)
        drain = lambda: components.prompt._drain_one_turn_before_prompt("synthetic", state)
    with pytest.raises(components.snapshot.RuntimeRecoveryPendingError, match="newer"):
        drain()
    if owner == "cli":
        assert state._one_turn_runtime_cleanup is newer and state._pending_one_turn_model_restore is scheduled
    else:
        assert state["one_turn_runtime_cleanup"] is newer and state["one_turn_model_restore"] is scheduled


@pytest.mark.parametrize("entry", ["chat", "_chat_run_agent"])
def test_actual_cli_entries_block_before_full_cli_import(components, entry):
    debt, scheduled, result = {"completed": True}, {"unused": True}, {"error": "original"}
    def busy(*args):
        raise components.mutation.RuntimeMutationBusyError("synthetic busy")
    state = SimpleNamespace(_one_turn_runtime_cleanup=debt, _pending_one_turn_model_restore=scheduled,
                            _restore_model_runtime_snapshot=busy, _last_turn_result=result)
    method = getattr(components.chat.CLIChatTurnMixin, entry)
    with pytest.raises(components.snapshot.RuntimeRecoveryPendingError):
        if entry == "chat":
            method(state, "synthetic prompt")
        else:
            method(state, SimpleNamespace(result=result), "synthetic prompt")
    assert state._one_turn_runtime_cleanup is debt and state._pending_one_turn_model_restore is scheduled
    assert state._last_turn_result is result and "cli" not in sys.modules
    assert "run_agent" not in sys.modules


def test_actual_tui_dispatch_refuses_before_row_write_or_admission(components, monkeypatch):
    debt, scheduled = {"completed": True}, {"unused": True}
    session = {"agent": SimpleNamespace(), "one_turn_runtime_cleanup": debt, "one_turn_model_restore": scheduled}
    def busy(*args):
        raise components.mutation.RuntimeMutationBusyError("synthetic busy")
    def owner_scope(target, *, hydrate_secrets):
        assert target is session and hydrate_secrets is False
        return contextlib.nullcontext()
    monkeypatch.setattr(components.prompt, "_session_profile_runtime_scope", owner_scope, raising=False)
    monkeypatch.setattr(components.prompt, "_restore_agent_model_runtime", busy, raising=False)
    for name in ("_ensure_session_db_row", "_admit_prompt_turn", "_prepare_turn_input", "_invoke_agent"):
        monkeypatch.setattr(components.prompt, name, _unexpected, raising=False)
    with pytest.raises(components.snapshot.RuntimeRecoveryPendingError):
        components.prompt._run_prompt_submit("synthetic-rid", "synthetic-sid", session, "synthetic prompt")
    assert session["one_turn_runtime_cleanup"] is debt and session["one_turn_model_restore"] is scheduled
    assert "tui_gateway.server" not in sys.modules and "run_agent" not in sys.modules
