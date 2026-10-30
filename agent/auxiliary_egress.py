"""Actual sending-route identity for auxiliary request authorization."""
from types import SimpleNamespace
from typing import Any


def auxiliary_route(client: Any, *, provider: str | None, model: str | None,
                    api_mode: str | None) -> SimpleNamespace:
    from agent.auxiliary_client import (
        AsyncBedrockAuxiliaryClient, BedrockAuxiliaryClient, _normalize_aux_provider,
    )

    normalized = _normalize_aux_provider(provider)
    mode = api_mode or ('codex_responses' if normalized == 'openai-codex' else 'chat_completions')
    if isinstance(client, (BedrockAuxiliaryClient, AsyncBedrockAuxiliaryClient)):
        mode = 'bedrock_converse'
    return SimpleNamespace(provider=normalized, model=str(model or ''),
        base_url=str(getattr(client, 'base_url', '') or ''), api_mode=str(mode))


def guard_auxiliary_context(agent: Any, route: Any) -> None:
    """Canonical history may be flattened here; a public SYSTEM is not input proof."""
    from agent.llm_egress_runtime import egress_enforcement_enabled
    from agent.remote_task_context import owned_remote_task, reject_remote_task_request

    if owned_remote_task(agent, route=route) and egress_enforcement_enabled():
        reject_remote_task_request(agent, route, 'unsupported_remote_task_auxiliary')
