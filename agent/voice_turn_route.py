"""Per-turn model route for voice chat: ``auxiliary.voice_chat``.

A voice turn (CLI voice input, a Desktop/TUI voice conversation, a messaging voice note) can run
on a faster model than the session's main one. The route binds at the end of turn setup, after the
system prompt and session row were built for the main model and after preflight compression
judged the history against the main model's window, and is undone before post-turn work (memory
sync, background review) so nothing outside the turn sees it. A turn whose request would not fit
under the voice model's compression threshold at turn start runs on the main model instead, so
switching to voice never compacts the conversation to suit a smaller model.

Surfaces mark the next turn with ``agent._voice_turn_pending = True``; it is consumed here. GPT-Live
delegations are not voice turns in this sense: the live model already owns the voice layer and
delegates real work to the session's model.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TASK = "voice_chat"

# Agent fields the route swap touches outside the ``_primary_runtime``-shaped snapshot. The
# reasoning-rejection flags are facts about one route: the voice model starts without the main
# model's and leaves its own behind (a learned floor is remembered per route instead).
_REJECTION_FIELDS = ("_reasoning_disable_rejected", "_reasoning_floor_required", "_reasoning_effort_rejected")
_EXTRA_FIELDS = (
    "_fallback_activated", "_fallback_index", "_provider_fallback_active", "_provider_fallback_route",
    "_rate_limit_backoff_count", "_credential_pool", "_credential_pool_entry_id", "_config_context_length",
    *_REJECTION_FIELDS,
)


def _route_target(agent: Any, cfg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The entry to bind, or None when the slot follows the main model (``auto`` + no model)."""
    provider = str(cfg.get("provider") or "").strip().lower()
    model = str(cfg.get("model") or "").strip()
    base_url = str(cfg.get("base_url") or "").strip()
    if provider in ("", "auto", "main"):
        if base_url:
            provider = "custom"
        elif model:
            provider = str(getattr(agent, "provider", "") or "").strip().lower()
        else:
            return None
    if not model:
        # Provider pinned without a model: that provider's fast auxiliary model.
        from agent.auxiliary_client import _get_aux_model_for_provider
        model = _get_aux_model_for_provider(provider, prefer_fast=True) or ""
    if not (provider and model):
        return None
    if not base_url and provider == str(agent.provider or "").lower() and model == agent.model:
        return None
    # ``provider: openai`` means the user's OpenAI endpoint, as on every other auxiliary task.
    from hermes_cli.runtime_provider_custom import expand_direct_api_alias
    provider, base_url = expand_direct_api_alias(provider, base_url or None)
    return {**cfg, "provider": provider, "model": model, "base_url": base_url or ""}


def _fits(agent: Any, messages: list[dict[str, Any]], system_prompt: str) -> bool:
    from agent.model_metadata import estimate_request_tokens_rough
    limit = getattr(getattr(agent, "context_compressor", None), "threshold_tokens", 0) or 0
    if not limit:
        return True
    tokens = estimate_request_tokens_rough(
        messages, system_prompt=system_prompt or "", tools=getattr(agent, "tools", None) or None)
    return tokens < limit


def _lowest_effort(agent: Any) -> Optional[str]:
    """The weakest enabled level the active route accepts when it cannot switch reasoning OFF, else
    None (OFF goes on the wire as configured). Static knowledge only; a route that 400s on the disable
    anyway is learned by turn_recovery and remembered by ``end_voice_turn_route``."""
    from agent.auxiliary_reasoning_floor import REASONING_FLOOR_EFFORT, known_reasoning_floor
    off = {"enabled": False}
    if known_reasoning_floor(off, agent.provider, agent.base_url, agent.model, TASK) is not off:
        return REASONING_FLOOR_EFFORT
    if agent.api_mode == "codex_responses":
        from agent.codex_responses_adapter import classify_responses_route
        from agent.transports.codex import _resolve_reasoning
        route = classify_responses_route(agent)
        # An enabled "none" clamps to "none" where the route has it, else to its weakest level
        # (None: the route takes no reasoning field at all).
        effort, _ = _resolve_reasoning(agent.model, {
            "provider": agent.provider, "base_url": agent.base_url, "is_codex_backend": route.is_codex_backend,
            "is_xai_responses": route.is_xai_responses, "reasoning_config": {"enabled": True, "effort": "none"}})
        return None if effort in (None, "none") else effort
    if agent.api_mode == "anthropic_messages":
        from agent.anthropic_adapter import _MANDATORY_THINKING_CLAUDE_SUBSTRINGS, _model_matches
        # Mandatory-thinking Claude omits the disable and thinks at its default effort.
        return REASONING_FLOOR_EFFORT if _model_matches(agent.model, _MANDATORY_THINKING_CLAUDE_SUBSTRINGS) else None
    return None


def _voice_reasoning(agent: Any, effort: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """``effort`` for the active route: reasoning OFF becomes the lowest valid level where OFF is
    not an option."""
    if not (isinstance(effort, dict) and effort.get("enabled") is False):
        return effort
    lowest = _lowest_effort(agent)
    return {"enabled": True, "effort": lowest} if lowest else effort


def _capture(agent: Any) -> dict[str, Any]:
    from agent.primary_runtime_snapshot import _build_primary_runtime_snapshot
    return {
        "snapshot": _build_primary_runtime_snapshot(agent, agent.api_mode),
        **{name: getattr(agent, name, None) for name in _EXTRA_FIELDS},
    }


def _reinstall(agent: Any, state: dict[str, Any]) -> None:
    from agent.route_binding import reinstall_runtime_snapshot
    reinstall_runtime_snapshot(agent, state["snapshot"])
    for name in _EXTRA_FIELDS:
        setattr(agent, name, state[name])


def _warn_once(agent: Any, target: dict[str, Any], reason: str) -> None:
    logger.warning("auxiliary.%s route %s/%s not used: %s; the voice turn runs on %s/%s",
                   TASK, target["provider"], target["model"], reason, agent.provider, agent.model)
    if getattr(agent, "_warned_voice_route", False):
        return
    agent._warned_voice_route = True
    emit = getattr(agent, "_emit_warning", None)
    if callable(emit):
        emit(f"⚠ Voice chat model {target['model']} via {target['provider']} unavailable ({reason}); "
             f"using {agent.model}.")


def _begin_voice_turn_route(agent: Any, messages: list[dict[str, Any]], system_prompt: Any, lease) -> Any:
    """Bind the voice route for a marked turn; returns the system prompt the turn should send."""
    if not getattr(agent, "_voice_turn_pending", False):
        return system_prompt
    agent._voice_turn_pending = False
    from agent.auxiliary_task_config import _get_auxiliary_task_config
    from hermes_constants import parse_reasoning_effort
    cfg = _get_auxiliary_task_config(TASK)
    effort = parse_reasoning_effort(cfg.get("reasoning_effort"))
    target = _route_target(agent, cfg)
    if target is None and effort is None:
        return system_prompt
    state: dict[str, Any] = {"reasoning_config": copy.deepcopy(getattr(agent, "reasoning_config", None))}
    agent._voice_route_state = state
    record = _register_voice(agent, state, lease)
    if target is not None:
        state.update(_capture(agent))
        reason = ""
        try:
            from agent.route_binding import bind_route_entry
            if bind_route_entry(agent, target, target["provider"], target["model"]) is None:
                reason = "provider not configured"
        except Exception as exc:  # health: allow BLE001 -- any bind failure falls back to the main model with a notice
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        if not reason and not _fits(agent, messages, system_prompt):
            reason = "the conversation is larger than its context window"
        if reason:
            _reinstall(agent, state)
            _warn_once(agent, target, reason)
            agent._voice_route_state = {"reasoning_config": state["reasoning_config"]}
            record.state = agent._voice_route_state
            return agent._cached_system_prompt or system_prompt
        from agent.chat_completion_helpers import rewrite_prompt_model_identity
        rewrite_prompt_model_identity(agent, agent.model, agent.provider)
        for name in _REJECTION_FIELDS:
            setattr(agent, name, False)
        agent._turn_route_task = TASK
        logger.info("Voice turn routed to %s (%s)", agent.model, agent.provider)
    if effort is not None:
        agent.reasoning_config = _voice_reasoning(agent, effort)
    return agent._cached_system_prompt or system_prompt


class _SessionRuntimeView:
    """``agent`` with the session's own route and effort in place of a live voice turn's."""

    def __init__(self, agent: Any, state: dict[str, Any]) -> None:
        snapshot = state.get("snapshot") or {}
        own = {name: snapshot[name] for name in
               ("model", "provider", "requested_provider", "base_url", "api_mode") if name in snapshot}
        if "anthropic_base_url" in snapshot:
            own["_anthropic_base_url"] = snapshot["anthropic_base_url"]
        own["reasoning_config"] = state.get("reasoning_config")
        self.__dict__.update(_agent=agent, _own=own)

    def __getattr__(self, name: str) -> Any:
        own = self.__dict__["_own"]
        return own[name] if name in own else getattr(self.__dict__["_agent"], name)


def session_runtime_view(agent: Any) -> Any:
    """What a surface reports or persists as the SESSION's model and effort. Mid voice turn the agent
    runs on the voice route (often reasoning off); reported as the chat's own setting, Desktop adopted
    it into the composer, and every new chat it opened inherited reasoning off."""
    state = getattr(agent, "_voice_route_state", None)
    return _SessionRuntimeView(agent, state) if state else agent


@dataclass
class _VoiceRecovery:
    state: dict[str, Any]
    revision: int
    selection_token: str
    preferred_snapshot: Any
    explicit_provenance: bool = True
    floor_recorded: bool = False
    credential_invalid: bool = False


def _register_voice(agent, state, lease, *, explicit=True):
    controller = lease.controller
    _, _, revision, selected, _ = controller.voice_snapshot()
    record = _VoiceRecovery(state, revision, selected, getattr(agent, '_primary_runtime', None), explicit)
    controller.register_voice(lease, record)
    return record


def _discard_projection(agent, record):
    if getattr(agent, '_voice_route_state', None) is record.state:
        agent._voice_route_state = None
        agent._turn_route_task = ''


def _propagate_validated_voice_credential(agent, old_token, new_token):
    """Update an exact captured native owner after its existing successful refresh.

    This consumes already validated rotation evidence; it never resolves a route
    or credential. An ambiguous backup holding the revoked token cannot recover.
    The caller owns the credential-refresh mutation reservation.
    """
    from agent.runtime_mutation import runtime_controller
    from hermes_cli.route_identity import normalize_route_base_url

    controller = runtime_controller(agent)
    record, _, revision, selected, _ = controller.voice_snapshot()
    if record is None or record.credential_invalid:
        return
    snapshot = record.state.get('snapshot')
    if not isinstance(snapshot, dict) or not isinstance(old_token, str) or not old_token:
        return
    fields = ('api_key', 'anthropic_api_key', 'compressor_api_key')
    kwargs = snapshot.get('client_kwargs')
    matches = lambda value: isinstance(value, str) and value == old_token
    if not (any(matches(snapshot.get(key)) for key in fields)
            or isinstance(kwargs, dict) and matches(kwargs.get('api_key'))):
        return
    saved_url = snapshot.get('anthropic_base_url')
    current_url = getattr(agent, '_anthropic_base_url', None)
    entry = record.state.get('_credential_pool_entry_id')
    current_entry = getattr(agent, '_credential_pool_entry_id', None)
    same_entry = entry is current_entry or (
        isinstance(entry, str) and isinstance(current_entry, str) and entry == current_entry)
    qualified = (
        record.explicit_provenance and record.revision == revision and record.selection_token == selected
        and getattr(agent, '_primary_runtime', None) is record.preferred_snapshot
        and snapshot.get('provider') == getattr(agent, 'provider', None) == 'anthropic'
        and snapshot.get('api_mode') == getattr(agent, 'api_mode', None) == 'anthropic_messages'
        and 'anthropic_base_url' in snapshot
        and (saved_url is None or isinstance(saved_url, str))
        and (current_url is None or isinstance(current_url, str))
        and normalize_route_base_url(saved_url) == normalize_route_base_url(current_url)
        and '_credential_pool' in record.state and '_credential_pool_entry_id' in record.state
        and hasattr(agent, '_credential_pool') and hasattr(agent, '_credential_pool_entry_id')
        and record.state['_credential_pool'] is getattr(agent, '_credential_pool', None)
        and same_entry and matches(snapshot.get('anthropic_api_key'))
        and isinstance(new_token, str) and bool(new_token)
    )
    if not qualified:
        record.credential_invalid = True
        return
    updated = dict(snapshot)
    for key in fields:
        if matches(updated.get(key)):
            updated[key] = new_token
    if isinstance(kwargs, dict) and matches(kwargs.get('api_key')):
        updated['client_kwargs'] = {**kwargs, 'api_key': new_token}
    updated['is_anthropic_oauth'] = agent._is_anthropic_oauth
    record.state['snapshot'] = updated


def _finish_voice_recovery(agent, record, lease):
    controller = lease.controller
    current, _, revision, selected, _ = controller.voice_snapshot()
    if (current is not record or revision != record.revision or selected != record.selection_token
            or getattr(agent, '_primary_runtime', None) is not record.preferred_snapshot):
        controller.finish_voice(record)
        _discard_projection(agent, record)
        return True
    if record.credential_invalid:
        controller.defer_voice(record)
        return False
    try:
        if 'snapshot' in record.state:
            if not record.floor_recorded and getattr(agent, '_reasoning_floor_required', False):
                from agent.auxiliary_reasoning_floor import remember_reasoning_floor
                remember_reasoning_floor(agent.provider, agent.base_url, {'model': agent.model}, None)
                record.floor_recorded = True
            _reinstall(agent, record.state)
        agent.reasoning_config = record.state['reasoning_config']
    except Exception:
        controller.defer_voice(record)
        logger.warning('Voice runtime restoration remains pending', exc_info=True)
        return False
    controller.finish_voice(record)
    _discard_projection(agent, record)
    return True


def begin_voice_turn_route(agent: Any, messages: list[dict[str, Any]], system_prompt: Any) -> Any:
    """Reserve before consuming the voice request or mutating the active route."""
    if not getattr(agent, '_voice_turn_pending', False):
        return system_prompt
    from agent.runtime_mutation import runtime_mutation
    with runtime_mutation(agent, 'begin_voice_turn_route') as lease:
        if not getattr(agent, '_voice_turn_pending', False):
            return system_prompt
        ticket = lease.controller.begin_voice_setup(lease)
        try:
            return _complete_voice_begin(agent, messages, system_prompt, lease)
        finally:
            lease.controller.finish_voice_setup(lease, ticket)


def _complete_voice_begin(agent, messages, system_prompt, lease):
    result = _begin_voice_turn_route(agent, messages, system_prompt, lease)
    record, debt, _, _, _ = lease.controller.voice_snapshot()
    if record is not None and debt is record:
        if not _finish_voice_recovery(agent, record, lease):
            from agent.primary_runtime_snapshot import RuntimeRecoveryPendingError
            raise RuntimeRecoveryPendingError('voice cleanup requested during route setup is pending')
        return agent._cached_system_prompt or system_prompt
    return result


def end_voice_turn_route(agent: Any):
    """Restore the captured active runtime, or retain cleanup debt without masking a turn result."""
    from agent.runtime_mutation import RuntimeMutationBusyError, runtime_controller, runtime_mutation
    controller = runtime_controller(agent)
    record, debt, revision, selected, _ = controller.voice_snapshot()
    if record is not None and debt is record:
        return 'deferred'  # Only the established next-turn recovery hook retries cleanup.
    projection = getattr(agent, '_voice_route_state', None)
    if record is None and projection is None and not controller.voice_setup_pending:
        return None
    # Acquisition is outside the installer exception handler: contention is not an install failure.
    scope = runtime_mutation(agent, 'end_voice_turn_route')
    try:
        lease = scope.__enter__()
    except RuntimeMutationBusyError:
        controller.request_voice_cleanup(record, expected_revision=revision, expected_selection=selected)
        return 'deferred'
    try:
        record = controller.voice_snapshot()[0]
        if record is None:
            projection = getattr(agent, '_voice_route_state', None)
            if projection is None:
                return None
            # A legacy active projection may be cleaned up now, but cannot attest a missing primary for retry.
            record = _register_voice(agent, projection, lease, explicit=False)
        return None if _finish_voice_recovery(agent, record, lease) else 'deferred'
    finally:
        scope.__exit__(None, None, None)


def recover_voice_cleanup(agent, lease):
    """Drain only exact existing cleanup debt on the established turn-start recovery path."""
    record = lease.controller.voice_snapshot()[1]
    if record is None:
        return True
    if record.preferred_snapshot is None and not record.explicit_provenance:
        return False
    return _finish_voice_recovery(agent, record, lease)
