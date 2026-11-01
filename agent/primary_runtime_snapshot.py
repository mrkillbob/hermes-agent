"""The ``_primary_runtime`` snapshot: the route a fallback turn restores on the next turn.

Both builders (agent construction, and a deliberate model switch) record the custom entry that owns
the route's credential, resolved against the config the route runs on. Restoring scopes the
credential pool by that record, never by a live ``config.yaml`` lookup, so an entry edited or
removed mid-session can't hand the session another account's key.
"""

from __future__ import annotations

from typing import Any

from agent.custom_entry_pool import SNAPSHOT_OWNER_KEY, runtime_entry_owner


def _snapshot_primary_runtime(agent):
    # Per-turn restoration snapshot: after a fallback, the next turn restores these so the
    # preferred model gets a fresh attempt.
    if agent._fallback_activated:
        # Startup never resolved a preferred runtime. The active fallback is not one.
        agent._primary_runtime = None
        return
    _cc = agent.context_compressor
    agent._primary_runtime = {
        "model": agent.model,
        "provider": agent.provider,
        "requested_provider": agent.requested_provider,
        SNAPSHOT_OWNER_KEY: runtime_entry_owner(agent.provider, agent.requested_provider, agent.base_url),
        "base_url": agent.base_url,
        "api_mode": agent.api_mode,
        "api_key": getattr(agent, "api_key", ""),
        "request_overrides": dict(getattr(agent, "request_overrides", {}) or {}),
        "client_kwargs": dict(agent._client_kwargs),
        "use_prompt_caching": agent._use_prompt_caching,
        "use_native_cache_layout": agent._use_native_cache_layout,
        "reasoning_echo_flag": getattr(agent, "_reasoning_echo_flag", False),
        # Engine state _try_activate_fallback() overwrites (getattr: plugin engines may lack them).
        "compressor_model": getattr(_cc, "model", agent.model),
        "compressor_base_url": getattr(_cc, "base_url", agent.base_url),
        "compressor_api_key": getattr(_cc, "api_key", ""),
        "compressor_provider": getattr(_cc, "provider", agent.provider),
        "compressor_context_length": _cc.context_length,
        "compressor_threshold_tokens": _cc.threshold_tokens,
    }
    if agent.api_mode == "anthropic_messages":
        agent._primary_runtime.update({
            "anthropic_api_key": agent._anthropic_api_key,
            "anthropic_base_url": agent._anthropic_base_url,
            "is_anthropic_oauth": agent._is_anthropic_oauth,
        })


def _build_primary_runtime_snapshot(agent, api_mode) -> dict[str, Any]:
    """The ``_primary_runtime`` record that persists a switch across turns."""
    cc = getattr(agent, "context_compressor", None) or None
    rt = {
        "model": agent.model,
        "provider": agent.provider,
        "requested_provider": agent.requested_provider,
        SNAPSHOT_OWNER_KEY: runtime_entry_owner(agent.provider, agent.requested_provider, agent.base_url),
        "base_url": agent.base_url,
        "api_mode": agent.api_mode,
        "api_key": getattr(agent, "api_key", ""),
        "client_kwargs": dict(agent._client_kwargs),
        "use_prompt_caching": agent._use_prompt_caching,
        "use_native_cache_layout": agent._use_native_cache_layout,
        "reasoning_config": dict(agent.reasoning_config) if getattr(agent, "reasoning_config", None) else None,
        "reasoning_echo_flag": getattr(agent, "_reasoning_echo_flag", False),
        # Overrides must travel with the switched-to identity or a later recovery/restore resurrects
        # PRE-switch overrides from the stale init snapshot.
        # See #75091.
        "request_overrides": dict(getattr(agent, "request_overrides", {}) or {}),
        "runtime_capabilities": dict(getattr(agent, "runtime_capabilities", {}) or {}),
        "compressor_model": getattr(cc, "model", agent.model),
        "compressor_base_url": getattr(cc, "base_url", agent.base_url),
        "compressor_api_key": getattr(cc, "api_key", ""),
        "compressor_provider": getattr(cc, "provider", agent.provider),
        "compressor_context_length": cc.context_length if cc else 0,
        "compressor_api_mode": getattr(cc, "api_mode", agent.api_mode),
        "compressor_threshold_tokens": cc.threshold_tokens if cc else 0,
    }
    if api_mode == "anthropic_messages":
        rt.update({
            "anthropic_api_key": agent._anthropic_api_key,
            "anthropic_base_url": agent._anthropic_base_url,
            "is_anthropic_oauth": agent._is_anthropic_oauth,
        })
    return rt


class RuntimeRecoveryPendingError(RuntimeError):
    """A captured active runtime still needs recovery before another turn."""


def restore_primary_runtime(agent) -> bool:
    """Recover an owed active route, then apply the existing preferred-runtime gates."""
    from agent import agent_runtime_helpers as helpers
    from agent.runtime_mutation import runtime_mutation
    with runtime_mutation(agent, 'restore_primary_runtime') as lease:
        if lease.controller.voice_snapshot()[1] is not None:
            from agent.voice_turn_route import recover_voice_cleanup
            if not recover_voice_cleanup(agent, lease):
                raise RuntimeRecoveryPendingError('active runtime recovery is still pending')
        if agent._fallback_activated and agent._primary_runtime is None:
            from agent.startup_primary_recovery import recover_pending_primary
            return recover_pending_primary(agent, lease)
        return _restore_resolved_primary_runtime(agent, lease)


def switch_model(agent, new_model, new_provider, api_key='', base_url='', api_mode='', capabilities=None):
    """Reserve an explicit selection and invalidate only superseded voice recovery."""
    from agent import agent_runtime_helpers as helpers
    from agent.runtime_mutation import runtime_mutation
    with runtime_mutation(agent, 'switch_model') as lease:
        result = helpers.switch_model(agent, new_model, new_provider, api_key, base_url, api_mode, capabilities)
        commit_runtime_selection(agent, lease)
        return result


def finish_turn_result(agent, result):
    """Clear transient turn state while preserving the completed result object."""
    agent._turn_preflight_display_snapshot = None
    agent._turn_received_provider_response = False
    return result


def commit_runtime_selection(agent, lease):
    """Invalidate only recovery owned by a superseded successful session selection."""
    lease.successful_selection()
    agent._pending_primary_intent = None
    agent._voice_route_state = None
    agent._turn_route_task = ''


def _restore_resolved_primary_runtime(agent, runtime_lease=None) -> bool:
    """Restore the primary runtime at the start of a new turn so fallback stays turn-scoped
    (long-lived CLI agents and the gateway's cached agents)."""
    from agent import agent_runtime_helpers as helpers
    if not agent._fallback_activated:
        # Reset the index even without activation: a failed _try_activate_fallback() can strand
        # _fallback_index past the chain end and silently block future fallbacks (#20465).
        agent._fallback_index = 0
        helpers._revert_credential_rotation(agent, runtime_lease=runtime_lease)
        return False
    rt = agent._primary_runtime
    primary_provider = str((rt or {}).get("provider") or "").strip().lower()
    primary_model = str((rt or {}).get("model") or "").strip()
    from agent.fallback_cooldown import _is_entitlement_rejected
    from hermes_cli.chat_catalog import is_known_non_chat_model
    if primary_model and (
        _is_entitlement_rejected(agent, primary_provider, primary_model)
        or is_known_non_chat_model(primary_model)
    ):
        # Unentitled (#106475) or already known non-chat: restoring would announce a recovery
        # that was never verified and re-fail every turn. Stay on the fallback.
        return False
    primary_runtime_base_url = str((rt or {}).get("base_url") or "")

    def _matches_primary(candidate) -> bool:
        return helpers.credential_pool_matches_provider(candidate, primary_provider, base_url=primary_runtime_base_url)

    def _load_primary_pool():
        """The primary's pool (entry-scoped for a custom entry); None when absent or provider-mismatched."""
        from agent.custom_entry_pool import load_runtime_pool
        loaded = load_runtime_pool(primary_provider, primary_runtime_base_url, (rt or {}).get("requested_provider"), rt or {})
        return loaded if loaded is not None and _matches_primary(loaded) else None
    if helpers._primary_quota_reopened_early(agent, primary_provider, primary_model, _matches_primary, _load_primary_pool):
        agent._rate_limited_until = 0
    if getattr(agent, "_rate_limited_until", 0) > helpers.time.monotonic():
        return False  # primary still in rate-limit cooldown, stay on fallback
    blocked, prefetched_pool, prefetched = helpers._primary_reset_gate_blocks(
        agent, rt, primary_provider, primary_runtime_base_url, _matches_primary, _load_primary_pool
    )
    if blocked:
        return False
    agent._restore_wait_logged = False
    fallback_route = getattr(agent, "_provider_fallback_route", None)
    if not (isinstance(fallback_route, (list, tuple)) and len(fallback_route) == 2):
        fallback_route = (getattr(agent, "model", ""), getattr(agent, "provider", ""))
    previous_model, previous_provider = (str(v or "unknown") for v in fallback_route)
    provider_fallback_active = bool(getattr(agent, "_provider_fallback_active", False))
    try:
        from agent.route_binding import reinstall_primary_runtime
        reinstall_primary_runtime(
            agent, rt, primary_provider, primary_model, _matches_primary, _load_primary_pool, prefetched_pool, prefetched,
            runtime_lease=runtime_lease,
        )
        helpers.logger.info("Primary runtime restored for new turn: %s (%s)", agent.model, agent.provider)
        agent._provider_fallback_active = False
        agent._provider_fallback_route = None
        if provider_fallback_active:
            # Notification surfaces are best-effort and must never undo a successful restore.
            with helpers.contextlib.suppress(Exception):
                agent._emit_diagnostic_status(
                    f"✅ Primary model restored: {agent.model} via {agent.provider}; "
                    f"fallback {previous_model} via {previous_provider} is no longer active."
                )
        return True
    except Exception as e:
        logger = helpers.logger
        logger.warning("Failed to restore primary runtime: %s", e, exc_info=True)
        return False



def try_activate_fallback(agent, reason=None, reset_at=None, *, retry_exhausted=False):
    """Reserve the existing fallback root before cooldown, index, pool or route mutation."""
    from agent import chat_completion_helpers as helpers
    from agent.runtime_mutation import runtime_mutation
    with runtime_mutation(agent, 'try_activate_fallback'):
        return helpers.try_activate_fallback(agent, reason, reset_at, retry_exhausted=retry_exhausted)


def try_recover_primary_transport(agent, api_error, *, retry_count, max_retries):
    """An absent preferred runtime must not retire or replace the active fallback client."""
    from agent import agent_runtime_helpers as helpers
    from agent.runtime_mutation import runtime_mutation
    with runtime_mutation(agent, 'try_recover_primary_transport'):
        if agent._primary_runtime is None:
            return False
        return helpers.try_recover_primary_transport(agent, api_error, retry_count=retry_count, max_retries=max_retries)


def recover_with_credential_pool(agent, *, status_code, has_retried_429, classified_reason=None,
                                 error_context=None, billing_unverified=False):
    """Reserve pool mutation before the existing recovery implementation benches or selects entries."""
    from agent import agent_runtime_helpers as helpers
    from agent.runtime_mutation import runtime_mutation
    with runtime_mutation(agent, 'recover_with_credential_pool') as lease:
        return helpers.recover_with_credential_pool(
            agent, status_code=status_code, has_retried_429=has_retried_429,
            classified_reason=classified_reason, error_context=error_context, billing_unverified=billing_unverified,
            runtime_lease=lease)
