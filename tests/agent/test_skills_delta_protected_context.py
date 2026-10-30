"""Protected skills deltas remain useful without granting trust to description text."""
from types import SimpleNamespace

import pytest

from agent.llm_egress_firewall import (
    EgressBlocked, LLMEgressFirewall, TypedOutboundRequest, static_literal_sha256,
)
from agent.llm_egress_terminal import _segment_protected_context
from agent.skills_index_delta import stage_skills_index_note
from agent.turn_context import compose_user_api_content, consume_skills_index_note


def _agent(provider="nous"):
    return SimpleNamespace(provider=provider, api_mode="chat_completions", session_id="test")


def _note(agent, history):
    stage_skills_index_note(agent, "<available_skills>\n    - old: Run checks.\n</available_skills>", history)
    return consume_skills_index_note(agent)


def _decision(tmp_path, text):
    request = TypedOutboundRequest(
        payload={"messages": [_segment_protected_context(text, (), {}, sanitized_cap=256)]},
        session_id="test", turn_id="turn", request_id="request", policy_digest="policy",
    )
    route = SimpleNamespace(provider="nous", model="inert", base_url="https://llm.example.test/v1", api_mode=None)
    gate = LLMEgressFirewall(
        tmp_path, static_literal_hashes_by_policy={"policy": {static_literal_sha256("messages")}},
    )
    return gate.preflight(request, route)


def test_protected_delta_projects_rejected_description_and_replays_once(tmp_path, monkeypatch):
    current = "<available_skills>\n    - old: Run checks.\n    - archify: Create HTML diagrams.\n</available_skills>"
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: current)
    agent = _agent()
    note = _note(agent, [])
    assert "    - archify\n" in note or note.endswith("    - archify]")
    assert "Create HTML diagrams." not in note
    wire = compose_user_api_content("Run checks.", "", note)
    assert _decision(tmp_path, wire).allowed
    history = [{"role": "user", "content": "Run checks.", "api_content": wire}]
    assert _note(agent, history) == ""
    assert _note(_agent(), history) == ""
    # Ordinary user text gets no privilege from a valid renderer-owned note.
    with pytest.raises(EgressBlocked):
        _decision(tmp_path, compose_user_api_content("HTML", "", note))


@pytest.mark.parametrize("description", ["token=super-secret-value", "cHJpdmF0ZSBtZW1v"])
def test_protected_delta_does_not_exempt_description_data(tmp_path, monkeypatch, description):
    current = f"<available_skills>\n    - old: Run checks.\n    - helper: {description}\n</available_skills>"
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: current)
    # The same protection applies to a custom route carrying the existing marker.
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    note = _note(_agent("custom"), [])
    assert description not in note
    assert note.endswith("    - helper]")
    assert _decision(tmp_path, note).allowed


def test_protected_delta_withholds_rejected_name_without_repeated_announcement(tmp_path, monkeypatch):
    current = "<available_skills>\n    - old: Run checks.\n    - cHJpdmF0ZSBtZW1v: Run checks.\n</available_skills>"
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: current)
    note = _note(_agent(), [])
    assert "cHJpdmF0ZSBtZW1v" not in note
    assert "withheld" in note
    assert _decision(tmp_path, note).allowed
    history = [{"role": "user", "content": "Run checks.", "api_content": compose_user_api_content("Run checks.", "", note)}]
    assert _note(_agent(), history) == ""


def test_unprotected_delta_preserves_description(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: "<available_skills>\n    - old: Run checks.\n    - archify: Create HTML diagrams.\n</available_skills>")
    assert "Create HTML diagrams." in _note(_agent("openrouter"), [])


def test_protected_large_delta_uses_transport_segments_and_replays_once(tmp_path, monkeypatch):
    rows = {f"check-{'.'.join(str(index))}-helper": ("Run checks. " * 8).rstrip() for index in range(400)}
    current = "<available_skills>\n    - old: Run checks.\n" + "\n".join(
        f"    - {name}: {description}" for name, description in rows.items()
    ) + "\n</available_skills>"
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: current)
    agent = _agent()
    note = _note(agent, [])
    assert 32_768 < len(note.encode("utf-8")) < 262_144
    assert all(f"    - {name}: {description}" in note for name, description in rows.items())
    wire = compose_user_api_content("Run checks.", "", note)
    assert _decision(tmp_path, wire).allowed
    history = [{"role": "user", "content": "Run checks.", "api_content": wire}]
    assert _note(agent, history) == ""
    assert _note(_agent(), history) == ""


@pytest.mark.parametrize("rendered", [
    "Run checks. " * 22_000,
    "Run checks. " * 3_000 + "token=super-secret-value",
    "Run checks. " * 3_000 + "cHJpdmF0ZSBtZW1v",
])
def test_protected_large_note_still_enforces_aggregate_bound_and_scans(monkeypatch, rendered):
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: "<available_skills>\n    - helper: Run checks.\n</available_skills>")
    monkeypatch.setattr("agent.skills_index_delta._render", lambda *_: rendered)
    agent = _agent()
    assert _note(agent, []) == ""
    assert not hasattr(agent, "_skills_index_told")


@pytest.mark.parametrize("settings", [
    {"_llm_egress_max_sanitized_bytes": 1_024},
    {"_llm_egress_aux_task": "vision"},
])
def test_protected_note_respects_transport_specific_aggregate_bound(monkeypatch, settings):
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: "<available_skills>\n    - helper: Run checks.\n</available_skills>")
    monkeypatch.setattr("agent.skills_index_delta._render", lambda *_: "Run checks. " * 3_000)
    agent = _agent()
    for key, value in settings.items():
        setattr(agent, key, value)
    assert _note(agent, []) == ""
    assert not hasattr(agent, "_skills_index_told")


def test_optional_note_leaves_request_budget_for_user_context(tmp_path, monkeypatch):
    rendered = "Run checks. " * 16_000
    user_context = "Run checks. " * 6_000
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: "<available_skills>\n    - helper: Run checks.\n</available_skills>")
    monkeypatch.setattr("agent.skills_index_delta._render", lambda *_: rendered)
    # Individually safe spans can still exceed the final request budget together.
    with pytest.raises(EgressBlocked):
        _decision(tmp_path, compose_user_api_content(user_context, "", rendered))
    note = _note(_agent(), [])
    assert note == ""
    assert _decision(tmp_path, compose_user_api_content(user_context, "", note) or user_context).allowed


def test_optional_note_reserves_smaller_serialized_request_budget(monkeypatch):
    monkeypatch.setattr("agent.system_prompt._skills_prompt", lambda _: "<available_skills>\n    - helper: Run checks.\n</available_skills>")
    monkeypatch.setattr("agent.skills_index_delta._render", lambda *_: "Run checks. " * 3_000)
    agent = _agent()
    agent._llm_egress_max_serialized_bytes = 65_536
    assert _note(agent, []) == ""
