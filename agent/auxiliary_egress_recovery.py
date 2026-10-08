"""Recover a denied auxiliary request only through an explicitly local route."""

import os

from agent.llm_egress_firewall import DestinationClass, classify_destination



def local_fallback_entry(entry, *, main_runtime=None):
    """Screen the router's concrete endpoint before credentials or catalogs resolve.

    Built-in OAuth/discovery arms do not honor entry URLs. Named custom and
    ordinary API-key arms honor explicit endpoints before their configured
    defaults. Never infer locality from a provider name or transport mode.
    """
    from agent import auxiliary_client as auxiliary

    raw_provider = str(entry.get("provider") or "").strip()
    if not raw_provider or not str(entry.get("model") or "").strip():
        return None
    provider = auxiliary._normalize_aux_provider(raw_provider)
    base_url = str(entry.get("base_url") or "").strip()
    try:
        from hermes_cli.runtime_provider import _get_named_custom_provider

        original_provider = raw_provider.lower()
        alias_identity = original_provider.removeprefix("custom:")
        named = None
        branch = auxiliary._EXPLICIT_PROVIDER_BRANCHES.get(provider)
        if branch is None or alias_identity in auxiliary._LOCAL_SERVER_ALIASES:
            if original_provider != provider:
                named = _get_named_custom_provider(original_provider, metadata_only=True)
            if named is None:
                named = _get_named_custom_provider(provider, metadata_only=True)
        if named:
            # Current named routes honor explicit endpoints before saved defaults.
            base_url = base_url or str(named.get("base_url") or "").strip()
        elif provider == "custom":
            # Pin the same concrete endpoint the generic custom arm would use.
            base_url = (
                base_url
                or str((main_runtime or {}).get("base_url") or "").strip()
                or str(auxiliary._runtime_main_value("base_url") or "").strip()
                or os.getenv("OPENAI_BASE_URL", "").strip()
                or auxiliary._read_main_field("base_url", readonly=True)
            )
        elif provider == "openrouter":
            # This dedicated API-key arm honors an explicit endpoint override.
            # Its saved pool/default host is remote and cannot imply locality.
            if not base_url:
                return None
        elif branch is not None:
            # OAuth/discovery branches ignore per-entry endpoint overrides.
            return None
        else:
            from hermes_cli.auth import PROVIDER_REGISTRY

            registered = PROVIDER_REGISTRY.get(provider)
            if (
                registered is None or registered.auth_type != "api_key"
                or provider in {"anthropic", "copilot", "azure-foundry"}
            ):
                return None
            env_url = (
                os.getenv(registered.base_url_env_var, "").strip()
                if registered.base_url_env_var else ""
            )
            # Z.AI can probe credentials before applying explicit endpoint overrides.
            if provider == "zai" and classify_destination(
                provider, env_url, "chat_completions"
            ) is not DestinationClass.LOOPBACK:
                return None
            base_url = base_url or env_url or registered.inference_base_url
    except Exception:
        return None
    if classify_destination(provider, base_url, "chat_completions") is not DestinationClass.LOOPBACK:
        return None
    return {**entry, "base_url": base_url}



def local_main_supports_vision(provider, model, *, base_url, api_key=""):
    """Honor known capability constraints without remote discovery after a denial."""
    from agent.image_routing import _supports_vision_override
    from agent.models_dev import get_model_capabilities
    from hermes_cli.config import load_config_readonly
    from hermes_cli.local_runtime.capabilities import is_managed_provider, managed_model_supports_vision

    try:
        config = load_config_readonly()
        supports = _supports_vision_override(config, provider, model)
        if supports is None and is_managed_provider(provider, base_url):
            endpoint = (base_url.rsplit("/v1", 1)[0], api_key if isinstance(api_key, str) else "")
            supports = managed_model_supports_vision(model, endpoint=endpoint)
        if supports is None:
            capabilities = get_model_capabilities(provider, model, allow_network=False, config=config)
            supports = capabilities.supports_vision if capabilities is not None else None
        if supports is None:
            from agent.model_metadata import detect_local_server_type, query_ollama_supports_vision
            key = api_key if isinstance(api_key, str) else ""
            if provider == "ollama" or detect_local_server_type(base_url, api_key=key) == "ollama":
                supports = query_ollama_supports_vision(model, base_url, api_key=key)
    except Exception:
        supports = None
    # Match ordinary fallback's existing unknown-capability behavior.
    return True if supports is None else bool(supports)



def is_local_fallback_client(client, provider):
    """Verify the resolved physical endpoint before any metadata probe."""
    base_url = getattr(client, "base_url", None)
    api_mode = getattr(client, "api_mode", None)
    return classify_destination(
        provider, str(base_url) if base_url is not None else None,
        api_mode if isinstance(api_mode, str) else None,
    ) in {DestinationClass.LOCAL_PROCESS, DestinationClass.LOOPBACK}


def local_fallback_steps(route, step_factory):
    """Try configured fallbacks, accepting only local-process or loopback routes."""
    # Resolve through the caller module so its routing/cache seams remain authoritative.
    from agent import auxiliary_client as auxiliary

    failed_provider = route.resolved_provider or "auto"
    failed_model = route.final_model
    failed_base_url = route.base_info
    sources = [
        auxiliary._try_configured_fallback_chain,
    ]
    if route.resolved_provider in {"", "auto"}:
        # Auto routes have two user-configured fallback layers. The main-agent
        # model is the final explicit-provider fallback, after the top-level
        # fallback_providers policy has been exhausted.
        sources.append(auxiliary._try_main_fallback_chain)
    sources.append(auxiliary._try_main_agent_model_fallback)
    visited: set[tuple[str, str, str, str]] = set()

    for source in sources:
        while True:
            if source is auxiliary._try_main_agent_model_fallback:
                client, model, label = source(
                    failed_provider,
                    route.task,
                    reason="egress blocked",
                    failed_model=failed_model,
                    failed_base_url=failed_base_url,
                    main_runtime=route.main_runtime,
                    excluded_identities=visited,
                    async_mode=route.async_mode,
                    local_only=True,
                )
            elif source is auxiliary._try_main_fallback_chain:
                client, model, label = source(
                    route.task,
                    failed_provider,
                    reason="egress blocked",
                    failed_model=failed_model,
                    failed_base_url=failed_base_url,
                    excluded_identities=visited,
                    async_mode=route.async_mode,
                    local_only=True,
                )
            else:
                client, model, label = source(
                    route.task,
                    failed_provider,
                    reason="egress blocked",
                    failed_model=failed_model,
                    failed_base_url=failed_base_url,
                    excluded_identities=visited,
                    async_mode=route.async_mode,
                    local_only=True,
                )
            if client is None:
                break

            destination = auxiliary._fallback_destination(
                route.task, client, model, label
            )
            provider = destination.provider or auxiliary._fallback_provider_from_label(label)
            base_url = destination.base_url or str(getattr(client, "base_url", "") or "")
            api_mode = destination.api_mode or getattr(client, "api_mode", None)
            identity = (provider, model or destination.model or "", base_url, api_mode or "")
            if identity in visited:
                # A selector that ignores the failed identity made no
                # progress. Stop this layer instead of cycling forever.
                break
            visited.add(identity)
            # Configuration can name an override the provider resolver ignored.
            # Only the resolved client can establish the physical HTTP endpoint.
            physical_base = getattr(client, "base_url", None)
            physical_mode = getattr(client, "api_mode", None)
            classification = classify_destination(
                provider, str(physical_base) if physical_base is not None else None,
                physical_mode if isinstance(physical_mode, str) else None,
            )
            if classification in {DestinationClass.LOCAL_PROCESS, DestinationClass.LOOPBACK}:
                auxiliary._record_route_info(route.route_info, provider, model)
                response, _ = yield from auxiliary._rung(
                    step_factory("fallback", (client, model, label)),
                    lambda exc: any(check(exc) for check, _ in auxiliary._FALLBACK_REASONS)
                    or auxiliary._is_transient_transport_error(exc),
                )
                if response is not None:
                    return response
                # A local candidate may have been quarantined by the call
                # step. Feed its complete identity back so the selector can
                # continue to the next healthy local candidate.
                failed_provider = provider
                failed_model = model or destination.model
                failed_base_url = base_url
                continue

            # _try_* returns the first usable candidate. Feed its complete
            # identity back as the failed route so the next call scans past
            # that remote candidate rather than returning it repeatedly.
            failed_provider = provider
            failed_model = model or destination.model
            failed_base_url = base_url
    return None



def stream_with_local_recovery(req, retry_kwargs, candidate_kwargs, *, task,
                               stream_options=None, route_info=None):
    """Recover a denied stream before returning it to its reassembly owner."""
    from agent import auxiliary_client as auxiliary
    from agent.llm_egress_firewall import EgressBlocked


    try:
        return send_stream(req.client, req.kwargs, req.request_provider, req.resolved_api_mode,
                           task=task, stream_options=stream_options)
    except EgressBlocked as first_err:
        def perform(step):
            return auxiliary._call_fallback_candidate_sync(
                *step.args, **candidate_kwargs, stream=True, stream_options=stream_options)

        result = auxiliary._drive_ladder(
            auxiliary._start_recovery_ladder(
                first_err, req, retry_kwargs, task=task, async_mode=False, route_info=route_info),
            perform,
        )
        if result is auxiliary._RERAISE_ORIGINAL:
            raise
        return result


def send_stream(client, kwargs, provider, api_mode, *, task, stream_options=None):
    """Preserve the stream contract for both primary and refreshed candidates."""
    from agent import auxiliary_client as auxiliary

    kwargs = dict(kwargs, stream=True)
    if stream_options:
        kwargs["stream_options"] = stream_options
    if task == "moa_aggregator" and isinstance(client, auxiliary.CodexAuxiliaryClient):
        return client.chat.completions.create(**kwargs)
    return auxiliary._relay_sync_stream(client, kwargs, provider=provider, api_mode=api_mode)
