"""Provider-specific output reservation used by context initialization."""
from contextlib import suppress


def _compressor_max_tokens(agent):
    """``agent.max_tokens``, or the native-Gemini adapter default when unset: generateContent
    still sends maxOutputTokens=65,535 and the threshold is pct×(window − max_tokens), so
    reserving 0 let the provider 400 before compaction fired."""
    if agent.max_tokens is not None:
        return agent.max_tokens
    with suppress(Exception):
        from agent.gemini_native_adapter import (
            GEMINI_DEFAULT_MAX_OUTPUT_TOKENS, is_native_gemini_base_url
        )
        _gemini_provider = str(agent.provider or "").strip().lower() in {
            "gemini", "google", "google-gemini", "google-ai-studio",
        }
        if _gemini_provider or is_native_gemini_base_url(agent.base_url):
            return GEMINI_DEFAULT_MAX_OUTPUT_TOKENS
    return None
