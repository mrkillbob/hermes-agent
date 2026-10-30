"""Fork-only regression (see FORK_PATCHES.md): split output excludes non-segment files that
merely share the ``part`` prefix (e.g. the ``party-stt.m4a`` transcode)."""

from unittest.mock import patch

from tools import transcription_chunking as tc


def test_split_returns_only_generated_segments(tmp_path):
    for name in ("part000.m4a", "part001.m4a", "party-stt.m4a", "part.txt"):
        (tmp_path / name).write_bytes(b"x")
    with patch.object(tc, "_run_quiet"):
        out = tc._split("ffmpeg", "in.wav", str(tmp_path), [1.0])
    assert [p.rsplit("/", 1)[-1] for p in out] == ["part000.m4a", "part001.m4a"]


def test_segmenter_failure_becomes_an_error_envelope(tmp_path):
    """Regression: ffmpeg failing at the segment-muxing step must not escape as an exception."""
    import subprocess

    audio = tmp_path / "big.wav"
    audio.write_bytes(b"x" * 2048)
    compact = tmp_path / "compact.m4a"
    compact.write_bytes(b"y" * 2048)

    with patch.object(tc, "_find_ffmpeg_binary", return_value="ffmpeg"), \
         patch.object(tc, "_transcode_audio_for_stt", return_value=(str(compact), None)), \
         patch.object(tc, "_probe_audio_duration", return_value=600.0), \
         patch.object(tc, "_silence_midpoints", return_value=[]), \
         patch.object(tc, "_run_quiet", side_effect=subprocess.TimeoutExpired("ffmpeg", 300)):
        result = tc.transcribe_oversized(str(audio), (1024, None), lambda part: {"success": True})

    assert result["success"] is False
    assert "segmenting failed" in result["error"]
