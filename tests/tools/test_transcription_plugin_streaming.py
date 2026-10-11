"""A plugin STT provider that declares ``streaming_capable`` is reachable through the live resolver.

``TranscriptionProvider.streaming_capable`` / ``open_stream_session`` are only an opt-in contract;
``tools.transcription_streaming`` must consult the registry for providers outside the built-in wires,
otherwise every surface silently records-then-uploads.
"""

from __future__ import annotations

import pytest

from agent import transcription_registry
from agent.transcription_provider import TranscriptionProvider, TranscriptionStreamSession
from tools import transcription_streaming


class _Session(TranscriptionStreamSession):
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def push_audio(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def finalize(self) -> dict:
        return {"success": True, "transcript": f"heard {len(self.chunks)}", "provider": "live-plugin"}

    def partial_transcript(self) -> str:
        return "partial text"


class _Plugin(TranscriptionProvider):
    def __init__(self, name: str, streaming: bool) -> None:
        self._name, self._streaming = name, streaming
        self.opened: dict | None = None

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def transcribe(self, file_path: str, **kw) -> dict:
        return {"success": True, "transcript": "batch", "provider": self._name}

    @property
    def streaming_capable(self) -> bool:
        return self._streaming

    def open_stream_session(self, *, language=None, prompt=None):
        self.opened = {"language": language, "prompt": prompt}
        return _Session()


@pytest.fixture(autouse=True)
def _registry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    transcription_registry._reset_for_tests()
    yield
    transcription_registry._reset_for_tests()


def _configure(tmp_path, provider: str, extra: str = "") -> None:
    (tmp_path / "config.yaml").write_text(f"stt:\n  provider: {provider}\n  streaming: true\n{extra}")


def test_streaming_capable_plugin_gets_a_live_session(tmp_path):
    _configure(tmp_path, "live-plugin", "  language: en\n  prompt: vocab\n")
    plugin = _Plugin("live-plugin", streaming=True)
    transcription_registry.register_provider(plugin)

    assert transcription_streaming.streaming_available() is True
    partials: list[str] = []
    session = transcription_streaming.open_streaming_session(on_partial=partials.append)
    assert session is not None
    assert plugin.opened == {"language": "en", "prompt": "vocab"}
    session.push_audio(b"\x01\x00" * 160)
    session.push_audio(b"\x02\x00" * 160)
    result = session.finalize(timeout=5)
    assert result == {"success": True, "transcript": "heard 2", "provider": "live-plugin"}
    assert partials[-1:] == ["partial text"]


def test_batch_only_plugin_keeps_file_transcription(tmp_path):
    _configure(tmp_path, "batch-plugin")
    plugin = _Plugin("batch-plugin", streaming=False)
    transcription_registry.register_provider(plugin)

    assert transcription_streaming.streaming_available() is False
    assert transcription_streaming.open_streaming_session() is None
    assert plugin.opened is None
