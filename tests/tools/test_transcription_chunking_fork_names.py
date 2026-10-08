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
