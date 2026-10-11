"""Custom endpoint discovery, credential identity and cached model-row policy.

Dependencies late-bind through models so its monkeypatch and cache ownership remain authoritative.
"""

from __future__ import annotations

from typing import Any, Optional, TypeGuard


def fetch_api_models(
    api_key: Optional[str], base_url: Optional[str], timeout: float = 5.0,
    api_mode: Optional[str] = None, headers: Optional[dict[str, str]] = None,
) -> Optional[list[str]]:
    """Fetch the list of available model IDs from the provider's ``/models`` endpoint."""
    from hermes_cli.models import probe_api_models
    result = probe_api_models(api_key, base_url, timeout=timeout, api_mode=api_mode, request_headers=headers)
    return result.get("models")


def _custom_endpoint_fingerprint(
    api_key: Any, api_mode: Optional[str], headers: Optional[dict[str, str]]) -> str:
    """Custom endpoints have no ``PROVIDER_REGISTRY`` slug, so hash exactly what callers pass to
    :func:`fetch_api_models`: a rotated ``api_key``, changed ``api_mode`` or edited ``extra_headers``
    each bust the cache entry. blake2b for the same CodeQL rationale as ``_credential_fingerprint``."""
    from hermes_cli.models import json
    import hashlib

    from agent.command_token_source import CommandTokenSource
    identity = api_key.cache_identity if isinstance(api_key, CommandTokenSource) else api_key
    blob = "|".join((identity or "", api_mode or "", json.dumps(headers or {}, sort_keys=True)))
    return hashlib.blake2b(blob.encode("utf-8", errors="replace"), digest_size=8).hexdigest()


def _cache_entry_valid(
    entry: Any, fp: str, *, allow_empty: bool = False) -> TypeGuard[dict[str, Any]]:
    """Well-formed cache row for fingerprint *fp*. Requires a numeric ``at`` so corrupt disk state
    degrades to a cache miss instead of raising; empty model lists are valid only when the caller
    opts into an authoritative empty catalog."""
    return (
        isinstance(entry, dict)
        and entry.get("fp") == fp
        and isinstance(entry.get("models"), list)
        and (allow_empty or bool(entry["models"]))
        and isinstance(entry.get("at"), (int, float))
        and not isinstance(entry.get("at"), bool))


def _cached_fetch_api_models(
    api_key: Any, base_url: Optional[str], *, timeout: float = 5.0,
    api_mode: Optional[str] = None, headers: Optional[dict[str, str]] = None,
    force_refresh: bool = False, cache_only: bool = False,
    fetch_models=None,
    ttl_seconds: int) -> Optional[list[str]]:
    """Disk-cached :func:`fetch_api_models` for custom endpoints. ``cache_only`` callers (GUI picker
    opens that must not block on a stopped local endpoint) still get a warm catalog instead of
    collapsing to the config-declared subset. ``fetch_models`` supplies native-aware discovery
    without minting a command token before cache admission."""
    from hermes_cli.models import _PROVIDER_MODELS_STALE_SERVE_MAX, _cache_entry, _cache_entry_valid, _chat_catalog_rows, _custom_endpoint_fingerprint, _load_provider_models_cache, _spawn_swr_refresh, _store_cache_entry, fetch_api_models, time
    from hermes_cli.model_switch_providers import _NativePickerModelList

    def _catalog(entry):
        rows = (_NativePickerModelList if entry.get("native_catalog") else list)(entry["models"])
        return _chat_catalog_rows(rows)

    def _entry(live, at=None):
        return {**_cache_entry(fp, live, at), "native_catalog": isinstance(live, _NativePickerModelList)}

    def _live():
        if fetch_models is not None:
            return fetch_models()
        from agent.command_token_source import materialize_probe_api_key
        return fetch_api_models(materialize_probe_api_key(api_key), base_url, timeout=timeout, api_mode=api_mode, headers=headers)

    normalized_url = str(base_url or "").strip().rstrip("/").lower()
    if not normalized_url:  # nothing to key the cache on
        return None if cache_only else _chat_catalog_rows(_live())

    # Key on URL AND credential fingerprint: N ``custom_providers`` rows can share one proxy URL
    # with distinct keys (#106184). A URL-only key let the last probe overwrite its siblings'
    # slot, so every other same-URL row failed the fingerprint check, got an empty catalog and
    # vanished from the no-probe pickers.
    fp = _custom_endpoint_fingerprint(api_key, api_mode, headers)
    cache_key = f"custom:{normalized_url}#{fp}"
    cache = _load_provider_models_cache()
    entry = cache.get(cache_key)
    now = time.time()
    native_row = isinstance(entry, dict) and entry.get("native_catalog") is True
    valid = not force_refresh and _cache_entry_valid(entry, fp, allow_empty=native_row)

    if valid:
        age = now - entry["at"]
        if age < ttl_seconds:
            return _catalog(entry)
        # An empty native catalog is authoritative only inside the TTL (as in
        # cached_provider_model_ids): never stale-serve it, or an Ollama that was model-less at
        # first open keeps an empty row for the whole stale window after models are pulled.
        if entry["models"] and age < _PROVIDER_MODELS_STALE_SERVE_MAX:
            # Stale-while-revalidate: serve now, refresh off-thread for the next open. cache_only
            # opens (GUI pickers that must not block on a stopped local server) take the same
            # non-blocking refresh: without it a locally loaded model stayed invisible for the
            # whole 7-day stale window unless the user found "Refresh Models" (#71169 class).
            def _refresh_custom():
                live = _live()
                return _entry(live) if live or isinstance(live, _NativePickerModelList) else None

            _spawn_swr_refresh(cache_key, _refresh_custom)
            return _catalog(entry)

    if cache_only:
        return None

    live = _live()
    if live or isinstance(live, _NativePickerModelList):
        stored = _entry(live, now)
        _store_cache_entry(cache_key, stored, cache)
        return _catalog(stored)
    # Live returned nothing (offline, timeout, auth hiccup): a stale same-fingerprint entry beats it
    # (non-empty only: an empty native row is not worth resurrecting over the generic fallback).
    if _cache_entry_valid(entry, fp):
        return _catalog(entry)
    return _chat_catalog_rows(live)
