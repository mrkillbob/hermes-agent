"""Normalize the message forms returned by auxiliary clients."""


def _coerce_llm_message(response):
    """Pull a message (dict, object, or str) out of a response-or-message value: dict-shaped
    responses/bare messages (compression, proxies) and ChatCompletion objects; MagicMock
    ``reasoning_*`` attrs are deliberately not strings."""
    if response is None or isinstance(response, str):
        return response
    if isinstance(response, dict):
        if "choices" not in response:
            return response
        choices = response.get("choices") or []
    else:
        choices = getattr(response, "choices", None)
        if not choices:
            return response
    return _message_field(choices[0], "message") if choices else None


def _message_field(msg, name):
    return msg.get(name) if isinstance(msg, dict) else getattr(msg, name, None)
