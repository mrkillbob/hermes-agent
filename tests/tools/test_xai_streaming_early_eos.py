"""Fork-only regression (see FORK_PATCHES.md): xAI end-of-audio before ``transcript.created``
keeps the queued PCM and sends it, then ``audio.done``, once the provider is ready."""

import json

from tools import transcription_streaming as ts


class _WS:
    def __init__(self, events):
        self.sent, self._events = [], list(events)

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout=None):
        if self._events:
            return json.dumps(self._events.pop(0))
        raise TimeoutError


def test_early_eos_flushes_backlog_before_audio_done():
    session = ts.XAIStreamingSession("k", "http://127.0.0.1:1/v1", "m", None)
    session._open(_WS([]))
    ws = _WS([{"type": "transcript.created"}, {"type": "transcript.done"}])
    session.push_audio(b"\x01\x00" * 160)
    session.end_audio()

    result = session._loop(ws)

    assert result["success"] is True
    assert ws.sent[0] == b"\x01\x00" * 160
    assert json.loads(ws.sent[-1]) == {"type": "audio.done"}
