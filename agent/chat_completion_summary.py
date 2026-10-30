"""Summary request projection for chat-completions transports."""

from __future__ import annotations

import contextlib
import logging

from agent.message_metadata import data_use_requirement

logger = logging.getLogger(__name__)


def iteration_summary_chat_kwargs(agent, api_messages: list) -> dict:
    """chat.completions.create kwargs for the summary, mirroring ChatCompletionsTransport.build_kwargs()."""
    from agent.chat_completion_helpers import _provider_preferences_for_agent, _reasoning_config_for_wire

    try:
        from agent.auxiliary_client import _fixed_temperature_for_model, OMIT_TEMPERATURE as _OMIT_TEMP
    except Exception:
        logger.debug("Optional summary temperature helper unavailable", exc_info=True)
        _fixed_temperature_for_model = _OMIT_TEMP = None
    raw_temp = (
        _fixed_temperature_for_model(agent.model, getattr(agent, "base_url", None))
        if _fixed_temperature_for_model is not None else None
    )
    temperature = None if raw_temp is _OMIT_TEMP else raw_temp
    provider_name = (agent.provider or "").strip().lower()
    # LM Studio uses top-level `reasoning_effort` (not extra_body.reasoning).
    is_lmstudio = provider_name == "lmstudio" and agent._supports_reasoning_extra_body()
    lm_reasoning_effort = agent._resolve_lmstudio_summary_reasoning_effort() if is_lmstudio else None

    extra_body = {}
    provider_profile = None
    with contextlib.suppress(Exception):
        from providers import get_provider_profile
        provider_profile = get_provider_profile(agent.provider)
    wire_reasoning_config = _reasoning_config_for_wire(agent)
    profile_owns_reasoning = bool(
        provider_profile
        and provider_profile.owns_reasoning_policy(
            supports_reasoning=agent._supports_reasoning_extra_body(),
        )
    )
    if (
        not is_lmstudio
        and not profile_owns_reasoning
        and agent._supports_reasoning_extra_body()
        and wire_reasoning_config is not None
    ):
        extra_body["reasoning"] = wire_reasoning_config
    if "nousresearch" in agent._base_url_lower:
        from agent.portal_tags import nous_portal_tags
        extra_body["tags"] = nous_portal_tags()

    summary_kwargs = {"model": agent.model, "messages": api_messages}
    build_api_kwargs = getattr(agent, "_build_api_kwargs", None)
    if build_api_kwargs is not None:
        with contextlib.suppress(Exception):
            tools = build_api_kwargs(api_messages).get("tools")
            if tools is not None:
                summary_kwargs["tools"] = tools
    if temperature is not None:
        summary_kwargs["temperature"] = temperature
    if agent.max_tokens is not None:
        summary_kwargs.update(agent._max_tokens_param(agent.max_tokens))
    if lm_reasoning_effort is not None:
        summary_kwargs["reasoning_effort"] = lm_reasoning_effort

    # Merge the profile's canonical body even when routing is unset (e.g. required Portal tags).
    provider_preferences = _provider_preferences_for_agent(agent)
    profile_extra_body = {}
    with contextlib.suppress(Exception):
        if provider_profile is not None:
            profile_reasoning_config = wire_reasoning_config
            if profile_reasoning_config is None:
                profile_reasoning_config = agent.reasoning_config
            profile_extra_body = provider_profile.build_extra_body(
                session_id=getattr(agent, "session_id", None), provider_preferences=provider_preferences or None,
                model=agent.model, base_url=agent.base_url, reasoning_config=agent.reasoning_config,
                data_use=data_use_requirement(api_messages))
            profile_extra_kwargs = provider_profile.build_api_kwargs_extras(
                reasoning_config=profile_reasoning_config,
                supports_reasoning=agent._supports_reasoning_extra_body(),
                model=agent.model, base_url=agent.base_url,
                session_id=getattr(agent, "session_id", None),
            )
            if profile_extra_kwargs:
                profile_extra_body = {**profile_extra_body, **(profile_extra_kwargs[0] or {})}
                summary_kwargs.update(profile_extra_kwargs[1] or {})
    if profile_extra_body:
        extra_body.update(profile_extra_body)

    def _is_openrouter() -> bool:
        return provider_name == "openrouter" or agent._is_openrouter_url()

    if provider_preferences and "provider" not in profile_extra_body and _is_openrouter():
        extra_body["provider"] = provider_preferences
    # Pareto Code router plugin — model-gated, same shape as the main-loop emission.
    _score = agent.openrouter_min_coding_score
    if agent.model == "openrouter/pareto-code" and _is_openrouter() and _score is not None and _score != "":
        with contextlib.suppress(TypeError, ValueError):
            _ps = float(_score)
            if 0.0 <= _ps <= 1.0:
                extra_body["plugins"] = [{"id": "pareto-router", "min_coding_score": _ps}]
    if extra_body:
        summary_kwargs["extra_body"] = extra_body
    return summary_kwargs
