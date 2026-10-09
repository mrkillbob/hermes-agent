"""Spoken-turn routing belongs to the admitted queue envelope, never the active turn."""

import threading
import types

import pytest

from tui_gateway import server


@pytest.fixture
def busy_session(monkeypatch):
    session = {
        "agent": types.SimpleNamespace(valid_tool_names=set()),
        "session_key": "queued-voice-session",
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": True,
        "transport": None,
        "attached_images": [],
    }
    monkeypatch.setitem(server._sessions, "queued-voice", session)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *args: False)
    monkeypatch.setattr(server, "_persist_queued_user_row", lambda *args: None)
    monkeypatch.setattr(server, "_replace_queued_user_row_for_turn", lambda *args, **kwargs: None)
    return session


def _submit(text, voice_turn):
    response = server._methods["prompt.submit"](
        "r1", {"session_id": "queued-voice", "text": text, "queued": True, "voice_turn": voice_turn})
    assert response["result"]["status"] == "queued"


@pytest.mark.parametrize("first_voice", [False, True])
def test_each_queued_turn_owns_its_voice_route(busy_session, monkeypatch, first_voice):
    busy_session["voice_turn"] = not first_voice
    _submit("first follow-up", first_voice)
    assert busy_session["voice_turn"] is (not first_voice)
    # An image gives the next input its own envelope rather than merging text.
    busy_session["attached_images"] = ["/unused/queued-image.png"]
    _submit("second follow-up", not first_voice)
    assert busy_session["voice_turn"] is (not first_voice)
    dispatched = []

    def run(rid, sid, session, text, **kwargs):
        dispatched.append((text, session.pop("voice_turn", False)))
        session["running"] = False

    monkeypatch.setattr(server, "_run_prompt_submit", run)
    busy_session["running"] = False
    assert server._drain_queued_prompt("r2", "queued-voice", busy_session)
    assert server._drain_queued_prompt("r3", "queued-voice", busy_session)
    assert dispatched == [("first follow-up", first_voice), ("second follow-up", not first_voice)]
    assert server._drain_queued_prompt("r4", "queued-voice", busy_session) is False


@pytest.mark.parametrize("first_voice,second_voice,expected", [(True, True, True), (True, False, False), (False, True, False)])
def test_mixed_typed_and_spoken_text_uses_the_normal_route(
        busy_session, monkeypatch, first_voice, second_voice, expected):
    _submit("first follow-up", first_voice)
    _submit("second follow-up", second_voice)
    dispatched = []
    monkeypatch.setattr(
        server, "_run_prompt_submit",
        lambda rid, sid, session, text, **kwargs: dispatched.append((text, session.pop("voice_turn", False))))
    busy_session["running"] = False
    assert server._drain_queued_prompt("r2", "queued-voice", busy_session)
    assert dispatched == [("first follow-up\n\nsecond follow-up", expected)]
