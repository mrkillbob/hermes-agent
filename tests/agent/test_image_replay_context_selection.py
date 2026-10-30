"""Retained images stay attached when a context engine's selection replaces the request."""

from __future__ import annotations

import json
from pathlib import Path

from agent.context_engine import ContextEngine
from agent.conversation_loop import _apply_context_engine_selection
from agent.image_routing import build_native_content_parts
from agent.session_persistence import _durable_content
from gateway.session_ingress_media import capture_native_media
from hermes_state import SessionDB

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc0f00f00050201ff2ecf3ca10000000049454e44ae426082")


class _Engine(ContextEngine):
    """Selects the transcript clones, the shape ``select_context`` documents for ``conversation_messages``."""

    name = "transcript-selector"

    def update_from_response(self, usage):
        pass

    def should_compress(self, prompt_tokens=None):
        return False

    def compress(self, messages, current_tokens=None, focus_topic=None):
        return messages

    def select_context(self, request_messages, *, conversation_messages=None, **kwargs):
        return conversation_messages


class _Agent:
    session_id = "s"
    provider, model = "custom", "fake-vision"
    _current_turn_timestamp = 10_000.0

    def __init__(self):
        self.context_compressor = _Engine()


class _Logger:
    def warning(self, *args, **kwargs):
        raise AssertionError(f"selection failed open: {args}")


def test_selection_built_from_the_transcript_keeps_replayed_images(tmp_path):
    from hermes_constants import get_hermes_home  # sandboxed per test by conftest

    (get_hermes_home() / "config.yaml").write_text(
        json.dumps({"agent": {"image_input_mode": "native"}}), encoding="utf-8")
    staged = tmp_path / "label.png"
    staged.write_bytes(_PNG)
    path = capture_native_media([staged])[0]["path"]
    parts, skipped = build_native_content_parts("What is on this label?", [Path(path)])
    assert not skipped

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="s", source="api_server")
    db.append_message("s", role="user", content=_durable_content(parts), timestamp=1000.0)
    db.append_message("s", role="assistant", content="reply", timestamp=1001.0)
    history = db.get_messages_as_conversation("s") + [{"role": "user", "content": "and the expiry date?"}]
    assert isinstance(history[0]["content"], str) and history[0]["content"].endswith("[screenshot]")

    request = [{"role": "user", "content": "unused: the engine replaces the request"}]
    selected = _apply_context_engine_selection(_Agent(), request, history, history[-1], logger=_Logger())

    assert selected[0]["content"] == parts
