"""Fork-only regression (see FORK_PATCHES.md): the upload limit is computed from the model a
``pre_transcription`` hook selected, not the pre-hook model."""

from unittest.mock import patch

from tools import transcription_tools as tt


def test_limit_uses_hook_selected_model(tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    seen = []

    def _limit(provider, model):
        seen.append(model)
        return (10**9, None)

    with patch.object(tt, "_load_stt_config", return_value={"provider": "openai"}), \
         patch.object(tt, "is_stt_enabled", return_value=True), \
         patch.object(tt, "_get_provider", return_value="openai"), \
         patch.object(tt, "_is_local_stt_provider", return_value=False), \
         patch.object(tt, "_trim_silence_for_cloud_stt", return_value=None), \
         patch.object(tt, "_apply_pre_transcription_hook",
                      return_value=("gpt-4o-transcribe", None, None)), \
         patch.object(tt, "upload_limit", side_effect=_limit), \
         patch.object(tt, "_route_stt_provider",
                      return_value={"success": True, "transcript": "ok", "provider": "openai"}):
        result = tt.transcribe_audio(str(audio), model="gpt-transcribe")

    assert result["success"] is True
    assert seen == ["gpt-4o-transcribe"]
