"""Recover an unresolved startup primary without treating its fallback as a snapshot.

Only canonical API-key built-ins using chat completions at a fixed SDK endpoint qualify. Custom entries,
aliases, auto and virtual routes retain their existing behavior. Nous has a separate
profile-owned, already-valid credential adoption path; it never refreshes authentication.
"""
from __future__ import annotations

from contextlib import suppress
from copy import copy
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrimaryIntent:
    provider: str
    model: str
    base_url: str
    api_mode: str


def pending_primary_intent(agent):
    if (not agent.provider or getattr(agent, 'requested_provider', None) != agent.provider or agent.base_url
            or getattr(agent, 'api_mode', None) != 'chat_completions' or not agent.model):
        return None
    if agent.provider == 'nous':
        from agent.startup_nous_adoption import pending_nous_intent
        return pending_nous_intent(agent)
    definition = _qualified_definition(agent.provider)
    return (PrimaryIntent(agent.provider, agent.model, definition.base_url.rstrip('/'), agent.api_mode)
            if definition is not None else None)


def _qualified_definition(provider):
    from hermes_cli.providers import get_provider, normalize_provider
    from agent.custom_entry_pool import named_custom_entry
    try:
        if normalize_provider(provider) != provider or named_custom_entry(provider) is not None:
            return None
        definition = get_provider(provider, allow_network=False)
        if (definition is None or definition.source not in ('models.dev', 'hermes')
                or definition.auth_type != 'api_key' or _has_resolution_hook(provider)
                or definition.transport not in ('openai_chat', 'codex_responses')
                or not definition.base_url.startswith('https://')):
            return None
        return definition
    except Exception:
        logger.debug('Startup primary metadata unavailable; retaining fallback', exc_info=True)
        return None


def _has_resolution_hook(provider):
    from providers import get_provider_profile
    from providers.base import ProviderProfile
    profile = get_provider_profile(provider)
    names = ('create_client', 'resolve_api_key_secret', 'runtime_base_url', 'prepare_pool_entry',
             'seed_pool_credentials', 'refresh_runtime_credentials')
    return profile is not None and any(
        getattr(getattr(profile, name), '__func__', None) is not getattr(ProviderProfile, name) for name in names)


class _DeferredWrites:
    """Record only setters the original compressor store actually supports."""

    def __init__(self, store):
        self.store = store
        self.calls = []

    def __getattr__(self, name):
        original = getattr(self.store, name)
        if not callable(original):
            raise AttributeError(name)

        def record(*args):
            self.calls.append((name, args))
        return record

    def flush(self):
        # ContextCompressor._durable_write already promises best-effort persistence.
        # Preserve that contract, but defer every effect until the runtime is coherent.
        for name, args in self.calls:
            try:
                getattr(self.store, name)(*args)
            except Exception:
                logger.debug('Startup recovery compression reset persistence failed: %s', name, exc_info=True)


@dataclass
class PreparedPrimary:
    fields: dict
    compressor_state: dict
    writes: _DeferredWrites

    def install(self, agent, *, flush=True):
        # Preparation ran all fallible calculations/callbacks. These are plain dict
        # publications under the runtime lease; retain the existing compressor object.
        old_fields = dict(agent.__dict__)
        old_compressor = dict(agent.context_compressor.__dict__)
        try:
            agent.context_compressor.__dict__.update(self.compressor_state)
            agent.__dict__.update(self.fields)
        except Exception:
            agent.__dict__.clear()
            agent.__dict__.update(old_fields)
            agent.context_compressor.__dict__.clear()
            agent.context_compressor.__dict__.update(old_compressor)
            raise
        if flush:
            self.writes.flush()


_LOAD_POOL = object()


def _prepare_primary(agent, intent, client, *, credential_pool=_LOAD_POOL):
    from agent.agent_init import _client_kwargs_from_routed, config_context_length_for_runtime
    from agent.context_compressor import ContextCompressor
    from agent.primary_runtime_snapshot import _build_primary_runtime_snapshot
    from agent.model_metadata import get_model_context_length
    from hermes_cli.config import load_config
    from hermes_cli.timeouts import get_provider_request_timeout
    from hermes_constants import resolve_reasoning_config
    from agent.credential_pool import load_pool
    from agent.agent_runtime_helpers import sync_credential_pool_entry_id
    from agent.chat_completion_helpers import _rescope_fallback_extra_body

    if type(agent.context_compressor) is not ContextCompressor:
        raise ValueError('startup recovery requires the built-in compressor preparation contract')
    staged = copy(agent)
    staged.__dict__ = dict(agent.__dict__)
    staged.context_compressor = copy(agent.context_compressor)
    staged.context_compressor.__dict__ = dict(agent.context_compressor.__dict__)
    writes = _DeferredWrites(getattr(agent.context_compressor, '_session_db', None))
    staged.context_compressor._session_db = writes
    staged._transport_cache = {}
    from agent.turn_recovery_codex import reset_codex_reasoning_replay
    reset_codex_reasoning_replay(staged)
    staged.model, staged.provider, staged.requested_provider = intent.model, intent.provider, intent.provider
    staged.base_url = str(client.base_url)
    staged.api_mode = intent.api_mode
    if staged.api_mode != 'chat_completions':
        raise ValueError('unsupported startup recovery transport')
    from agent.agent_runtime_helpers import _resolve_switch_destination
    staged.api_mode, _, staged.runtime_capabilities = _resolve_switch_destination(
        staged, intent.model, intent.provider, staged.base_url, staged.api_mode, None,
        agent.provider, intent.provider)
    staged.api_key = client.api_key
    timeout = get_provider_request_timeout(intent.provider, intent.model)
    if timeout is not None:
        client.timeout = timeout
    staged.client = client
    staged._anthropic_client = None
    staged._client_kwargs = _client_kwargs_from_routed(client, get_provider_request_timeout(intent.provider, intent.model))
    config = load_config() or {}
    staged._config_context_length = config_context_length_for_runtime(staged, config)
    staged.reasoning_config = resolve_reasoning_config(config, intent.model)
    staged._reasoning_echo_flag = staged._read_reasoning_echo_from_config()
    staged._credential_pool = load_pool(intent.provider) if credential_pool is _LOAD_POOL else credential_pool
    staged._credential_pool_entry_id = None
    sync_credential_pool_entry_id(staged)
    staged.request_overrides = dict(getattr(agent, 'request_overrides', {}) or {})
    _rescope_fallback_extra_body(staged, agent.model, agent.provider, agent.base_url)
    staged._use_prompt_caching, staged._use_native_cache_layout = staged._anthropic_prompt_cache_policy(
        provider=intent.provider, base_url=staged.base_url, api_mode=staged.api_mode, model=intent.model)
    if getattr(agent, '_cache_disabled', False):
        staged._use_prompt_caching = staged._use_native_cache_layout = False
    context_length = get_model_context_length(
        intent.model, base_url=staged.base_url, api_key=staged.api_key, provider=intent.provider,
        config_context_length=staged._config_context_length, custom_providers=getattr(agent, '_custom_providers', None))
    staged.context_compressor.update_model(model=intent.model, context_length=context_length,
        base_url=staged.base_url, api_key=staged.api_key, provider=intent.provider, api_mode=staged.api_mode)
    staged.context_compressor._session_db = getattr(agent.context_compressor, '_session_db', None)
    staged._primary_runtime = _build_primary_runtime_snapshot(staged, staged.api_mode)
    names = ('model', 'provider', 'requested_provider', '_base_url', '_base_url_lower', '_base_url_hostname', 'api_mode', 'api_key',
             'client', '_anthropic_client', '_client_kwargs', '_config_context_length', 'reasoning_config',
             '_reasoning_echo_flag', '_credential_pool', '_credential_pool_entry_id', 'request_overrides',
             '_use_prompt_caching', '_use_native_cache_layout', '_primary_runtime', '_transport_cache',
             'runtime_capabilities', '_codex_reasoning_replay_enabled', '_codex_reasoning_replay_rejected')
    fields = {name: getattr(staged, name) for name in names if hasattr(staged, name)}
    fields.update(_pending_primary_intent=None, _fallback_activated=False, _fallback_index=0,
                  _provider_fallback_active=False, _provider_fallback_route=None, _consecutive_stale_streams=0,
                  _credential_pool_revert_id=None, _rate_limit_backoff_count=0, _compression_feasibility_checked=False)
    return PreparedPrimary(fields, dict(staged.context_compressor.__dict__), writes)


def recover_pending_primary(agent, lease):
    """Resolve in the caller's existing profile/secret scope; never refresh it here."""
    if lease.agent is not agent:
        raise ValueError('startup recovery requires the owning runtime lease')
    with lease.controller.mutex:
        lease._check_owner_locked()
    intent = getattr(agent, '_pending_primary_intent', None)
    from agent.startup_nous_adoption import NousPrimaryIntent, recover_nous_adoption
    if isinstance(intent, NousPrimaryIntent):
        return recover_nous_adoption(agent, lease, profile_home=intent.profile_home, model=intent.model)
    if not isinstance(intent, PrimaryIntent):
        return False
    from agent.auxiliary_client import resolve_provider_client
    from agent.local_text_policy import primary_client_scope
    from agent.credential_pool_readonly import readonly_pool_resolution
    definition = _qualified_definition(intent.provider)
    if (definition is None or definition.base_url.rstrip('/') != intent.base_url
            or intent.api_mode != 'chat_completions'):
        return False
    client = None
    try:
        with primary_client_scope(), readonly_pool_resolution():
            client, _ = resolve_provider_client(intent.provider, model=intent.model, raw_codex=True)
            if client is None:
                return False
            if str(client.base_url).rstrip('/') != intent.base_url or not isinstance(client.api_key, str):
                client.close()
                return False
            prepared = _prepare_primary(agent, intent, client)
        old_client = agent.client
        prepared.install(agent)
    except Exception:
        if client is not None:
            with suppress(Exception):
                client.close()
        logger.debug('Startup primary remains unavailable; retaining fallback', exc_info=True)
        return False
    if old_client is not None and old_client is not agent.client:
        with suppress(Exception):
            agent._retire_shared_openai_client(old_client, reason='startup_primary_recovered')
    with suppress(Exception):
        agent._emit_pending_fallback_notice()
        agent._clear_status_buffer()
        agent._emit_diagnostic_status(f'Primary model restored: {intent.model} via {intent.provider}.')
    return True
