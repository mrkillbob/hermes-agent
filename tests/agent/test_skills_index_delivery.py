"""Late-bound route checks own only the current renderer span, never visible markers."""
from types import SimpleNamespace

import pytest

from agent.llm_egress_firewall import EgressBlocked
from agent.message_content import flatten_message_text
from agent.skills_index_delta import stage_skills_index_note
from agent.turn_context import _merge_gateway_notes, _stamp_api_content_sidecar, build_api_messages
from hermes_state import SessionDB
from tests.agent.test_skills_delta_protected_context import _decision

PROMPT = "<available_skills>\n    - old: Run checks.\n</available_skills>"
CURRENT = "<available_skills>\n    - old: Run checks.\n    - archify: Create HTML diagrams.\n</available_skills>"


def _stage(monkeypatch, *, multimodal=False, user="Run checks.", hook="Run checks."):
    monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: CURRENT)
    agent = SimpleNamespace(provider="openrouter", api_mode="chat_completions", session_id="test",
                            _current_turn_timestamp=1.0, ephemeral_system_prompt="",
                            _copy_reasoning_content_for_api=lambda *_: None,
                            _should_sanitize_tool_calls=lambda: False)
    message = {"role": "user", "content": [{"type": "text", "text": user}] if multimodal else user}
    assert stage_skills_index_note(agent, PROMPT, [])
    context = _merge_gateway_notes(agent, [message], 0, hook)
    if multimodal:
        from agent.turn_context import _append_multimodal_context
        _append_multimodal_context(agent, message, "", context, preflight_compressed=False)
    else:
        _stamp_api_content_sidecar(agent, [message], 0, "", context, preflight_compressed=False)
    return agent, message


def _live(message):
    return flatten_message_text(message.get("api_content") or message["content"])


def _wire(agent, message):
    return build_api_messages(agent, [message], current_turn_user_idx=0, ext_prefetch_cache="",
                              plugin_user_context="", moa_config=None, active_system_prompt="")[0][0]["content"]


@pytest.mark.parametrize("multimodal", [False, True])
@pytest.mark.parametrize("preflushed", [False, True])
def test_actual_bound_route_projects_current_note_and_persists_bytes(tmp_path, monkeypatch, multimodal, preflushed):
    from agent.session_persistence import _durable_content

    agent, message = _stage(monkeypatch, multimodal=multimodal)
    original = _live(message)
    assert "Create HTML diagrams." in original  # Main route compatibility; nothing has been built yet.
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("test", source="cli")
        if preflushed:
            message["_row_id"] = db.append_message("test", "user", _durable_content(message["content"]),
                                                  api_content=message.get("api_content"))
            agent._session_db = db
        # Voice and fallback both rebuild API messages after the active route binds.
        agent.provider = "nous"
        wire = _wire(agent, message)
        text = flatten_message_text(wire)
        assert "Create HTML diagrams." not in text
        assert "    - archify" in text
        assert _decision(tmp_path, text).allowed
        assert flatten_message_text(_wire(agent, message)) == text
        agent.provider = "openrouter"
        assert flatten_message_text(_wire(agent, message)) == text  # Never restore raw data.
        if not preflushed:
            db.append_message("test", "user", _durable_content(message["content"]), api_content=message.get("api_content"))
        stored = db.get_messages("test")[0]
        replay = stored.get("api_content") or stored["content"]
        assert flatten_message_text(replay) == text
        agent.provider = "nous"
        assert not stage_skills_index_note(agent, PROMPT, [stored])
    finally:
        db.close()


@pytest.mark.parametrize("where", ["user", "hook"])
def test_route_projection_never_admits_unrelated_unsafe_text(tmp_path, monkeypatch, where):
    agent, message = _stage(monkeypatch, **{where: "HTML"})
    agent.provider = "nous"
    text = flatten_message_text(_wire(agent, message))
    assert "Create HTML diagrams." not in text
    assert "HTML" in text
    with pytest.raises(EgressBlocked):
        _decision(tmp_path, text)


@pytest.mark.parametrize("expiry", ["copied-message", "changed-envelope", "new-turn"])
def test_expired_live_ownership_cannot_repair_marker_text(tmp_path, monkeypatch, expiry):
    agent, message = _stage(monkeypatch)
    if expiry == "copied-message":
        message = dict(message)
    elif expiry == "changed-envelope":
        message["api_content"] = "Run checks.\n\n" + message["api_content"]
    else:
        stage_skills_index_note(agent, PROMPT, [])  # New turn expires the old record.
    agent.provider = "nous"
    with pytest.raises(EgressBlocked):
        _decision(tmp_path, flatten_message_text(_wire(agent, message)))


def test_durable_row_mismatch_stops_projection_and_preserves_original(tmp_path, monkeypatch):
    agent, message = _stage(monkeypatch)
    original = message["api_content"]
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("test", source="cli")
        message["_row_id"] = db.append_message("test", "user", "Changed by another writer.", api_content=original)
        agent._session_db = db
        agent.provider = "nous"
        with pytest.raises(RuntimeError, match="durable row changed"):
            _wire(agent, message)
        assert message["api_content"] == original
        assert db.get_messages("test")[0]["api_content"] == original
    finally:
        db.close()


def test_protected_route_budget_withholds_only_owned_note_and_restores_accounting(tmp_path, monkeypatch):
    agent, message = _stage(monkeypatch)
    previous = agent._skills_index_delivery.previous_told
    agent.provider = "nous"
    agent._llm_egress_max_serialized_bytes = 100
    wire = _wire(agent, message)
    assert "skills available" not in wire
    assert "Run checks." in wire
    assert agent._skills_index_told == previous
    assert _decision(tmp_path, wire).allowed


@pytest.mark.parametrize("multimodal", [False, True])
def test_durable_projection_cas_rejects_changed_sidecar_or_multimodal_row(tmp_path, monkeypatch, multimodal):
    from agent.session_persistence import _durable_content

    agent, message = _stage(monkeypatch, multimodal=multimodal)
    original = _live(message)
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("test", source="cli")
        durable = _durable_content(message["content"])
        row_id = db.append_message("test", "user", durable, api_content=message.get("api_content"))
        message["_row_id"] = row_id
        agent._session_db = db
        if multimodal:
            assert db.set_user_message_content("test", row_id, "Independent writer.") == 1
        else:
            assert db.set_message_api_content("test", row_id, durable, "Independent writer.") == 1
        changed = db.get_messages("test")[0]
        agent.provider = "nous"
        with pytest.raises(RuntimeError, match="durable row changed"):
            _wire(agent, message)
        assert flatten_message_text(message.get("api_content") or message["content"]) == original
        assert db.get_messages("test")[0] == changed
    finally:
        db.close()


@pytest.mark.parametrize("transition", ["voice", "fallback"])
def test_route_orchestration_rebuild_projects_owned_note(tmp_path, monkeypatch, transition):
    from agent.route_binding import bind_route_entry
    from agent.turn_retry_state import TurnRetryState
    from agent.voice_turn_route import begin_voice_turn_route

    agent, message = _stage(monkeypatch)
    agent.model = "inert-main"
    agent.requested_provider = "openrouter"
    agent.base_url = "https://openrouter.ai/api/v1"
    agent._client_kwargs = {}
    agent._use_prompt_caching = agent._use_native_cache_layout = False
    agent.reasoning_config = None
    agent.context_compressor = None
    agent._cached_system_prompt = PROMPT
    agent._anthropic_prompt_cache_policy = lambda **_: (False, False)
    agent._ensure_lmstudio_runtime_loaded = lambda: None
    agent._replace_primary_openai_client = lambda **_: None
    entry = {"provider": "custom", "model": "inert-voice", "base_url": "https://llm.example.test/v1", "api_key": "sk-fake-inert", "api_mode": "chat_completions"}
    # Real config/router/client construction, never a provider completion.
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("auxiliary:\n  voice_chat:\n    provider: custom\n    model: inert-voice\n    base_url: https://llm.example.test/v1\n    api_key: sk-fake-inert\n    api_mode: chat_completions\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    before = _wire(agent, message)
    assert "Create HTML diagrams." in before
    try:
        if transition == "voice":
            agent._voice_turn_pending = True
            begin_voice_turn_route(agent, [message], PROMPT)
        else:
            from agent.conversation_loop import _arm_fallback_restart
            assert bind_route_entry(agent, entry, "custom", "inert-voice") is not None
            retry = TurnRetryState()
            _arm_fallback_restart(agent, [{"role": "user", "content": before}], PROMPT, retry)
            assert retry.restart_with_rebuilt_messages
        assert agent.provider == "custom"
        # Worker routing marks this custom remote as protected only after binding.
        monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
        wire = _wire(agent, message)
        assert "Create HTML diagrams." not in wire
        assert _decision(tmp_path, wire).allowed
    finally:
        client = getattr(agent, "client", None)
        if client is not None:
            client.close()


def test_unrevalidated_route_rebuild_control_is_blocked(tmp_path, monkeypatch):
    agent, message = _stage(monkeypatch)
    agent.provider = "nous"
    # Inert control for the former route-binding gap: no outgoing provider call.
    monkeypatch.setattr("agent.skills_index_delivery.revalidate_skills_note", lambda *_: None)
    with pytest.raises(EgressBlocked):
        _decision(tmp_path, _wire(agent, message))


@pytest.mark.parametrize("multimodal", [False, True])
def test_note_already_built_for_main_route_is_projected_on_fallback_copy_only(tmp_path, monkeypatch, multimodal):
    from agent.session_persistence import _durable_content

    agent, message = _stage(monkeypatch, multimodal=multimodal)
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("test", source="cli")
        message["_row_id"] = db.append_message("test", "user", _durable_content(message["content"]),
                                              api_content=message.get("api_content"))
        agent._session_db = db
        sent = flatten_message_text(_wire(agent, message))  # The main route's request carried the raw note.
        assert "Create HTML diagrams." in sent
        live, row, told = _live(message), db.get_messages("test")[0], agent._skills_index_told
        agent.provider = "nous"  # A protected fallback after the provider already saw the raw note.
        text = flatten_message_text(_wire(agent, message))
        assert "Create HTML diagrams." not in text
        assert "    - archify" in text
        assert _decision(tmp_path, text).allowed
        assert _live(message) == live  # Bytes already sent stay in the live message,
        assert db.get_messages("test")[0] == row  # in the persisted transcript,
        assert agent._skills_index_told == told  # and in the accounting.
        agent.provider = "openrouter"
        assert flatten_message_text(_wire(agent, message)) == sent  # The main route's cached prefix is intact.
    finally:
        db.close()


@pytest.mark.parametrize("built", [False, True])
@pytest.mark.parametrize("multimodal", [False, True])
def test_compaction_copy_inherits_ownership_through_reanchor(tmp_path, monkeypatch, multimodal, built):
    from agent.context_compressor import _fresh_compaction_message_copy
    from agent.turn_context_compaction import _reanchor

    agent, message = _stage(monkeypatch, multimodal=multimodal)
    if built:
        assert "Create HTML diagrams." in flatten_message_text(_wire(agent, message))
    copy = _fresh_compaction_message_copy(message)  # In-loop compression rebuilds the protected tail.
    assert _reanchor(agent, [copy], copy["content"]) == 0
    agent.provider = "nous"
    text = flatten_message_text(_wire(agent, copy))
    assert "Create HTML diagrams." not in text
    assert _decision(tmp_path, text).allowed


def test_compaction_rewritten_row_does_not_inherit_ownership(tmp_path, monkeypatch):
    from agent.turn_context_compaction import _reanchor

    agent, message = _stage(monkeypatch)
    rewritten = {**message, "content": message["content"] + " "}  # Merged into the tail: a new content object.
    assert _reanchor(agent, [rewritten], rewritten["content"]) == 0
    agent.provider = "nous"
    with pytest.raises(EgressBlocked):
        _decision(tmp_path, flatten_message_text(_wire(agent, rewritten)))
