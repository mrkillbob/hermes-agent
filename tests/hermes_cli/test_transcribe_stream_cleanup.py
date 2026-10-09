"""Fork-only regression (see FORK_PATCHES.md): a non-object first frame on
/api/audio/transcribe-stream still cancels the provider session and forwarder."""

import threading

import pytest
from starlette.testclient import TestClient

import hermes_cli.web_routers.audio as audio_router


@pytest.mark.parametrize("frame", ["[]", "3", "null"])
def test_non_object_first_frame_cancels_session(monkeypatch, frame):
    from hermes_cli.web_server import app

    cancelled = threading.Event()

    class _Session:
        def cancel(self):
            cancelled.set()

    monkeypatch.setattr(audio_router, "_ws_auth_ok", lambda ws: True)
    monkeypatch.setattr(audio_router, "_ws_request_is_allowed", lambda ws: True)
    monkeypatch.setattr("tools.transcription_streaming.open_streaming_session",
                        lambda on_partial: _Session())

    with TestClient(app).websocket_connect("/api/audio/transcribe-stream") as ws:
        ws.send_text(frame)
        ws.close()

    # The handler runs on the server's loop thread; give it time to reach its cleanup.
    assert cancelled.wait(timeout=10)
