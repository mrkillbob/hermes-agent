"""Bounded Feishu webhook parsing and authentication before delivery admission."""

import asyncio
import hmac
import json
import logging
from typing import Any

# Bundled profiles import this package under different synthetic namespaces.
# Use one canonical owner for capacity; profile credentials stay on the adapter.
from plugins.platforms.feishu.webhook_capacity import capacity as _PROCESS_CAPACITY

from . import adapter as feishu

logger = logging.getLogger(__name__)

# Non-queuing admission bounds body buffers/slow readers across all profiles.
# Per-adapter peer capacity leaves room without keeping denial history.
_MAX_ACTIVE_REQUESTS = 32
_MAX_ACTIVE_PER_PEER = 8


def _parse_payload(body: bytes) -> dict:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("webhook envelope must be an object")
    for key in ("header", "event"):
        if payload.get(key) is not None and not isinstance(payload[key], dict):
            raise ValueError(f"{key} must be an object")
    if payload.get("type") == "url_verification" and not isinstance(payload.get("challenge"), str):
        raise ValueError("challenge must be text")
    # Namespace conversion is recursive; reject excessive depth before it runs.
    containers = [(payload, 0)]
    while containers:
        container, depth = containers.pop()
        if depth > 32:
            raise ValueError("webhook envelope is too deeply nested")
        values = container.values() if isinstance(container, dict) else container
        containers.extend((item, depth + 1) for item in values if isinstance(item, (dict, list)))
    return payload


async def _read_body(adapter: Any, request: Any, remote_ip: str) -> Any:
    headers = getattr(request, "headers", {}) or {}
    content_type = str(headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
    if content_type and content_type != "application/json":
        return adapter._webhook_reject(remote_ip, "415", 415, "Unsupported Media Type")
    content_length = getattr(request, "content_length", None)
    if content_length is not None and content_length > feishu._FEISHU_WEBHOOK_MAX_BODY_BYTES:
        return adapter._webhook_reject(remote_ip, "413", 413, "Request body too large")
    try:
        return await asyncio.wait_for(
            feishu._read_limited_feishu_webhook_body(request, feishu._FEISHU_WEBHOOK_MAX_BODY_BYTES),
            timeout=feishu._FEISHU_WEBHOOK_BODY_TIMEOUT_SECONDS,
        )
    except ValueError:
        return adapter._webhook_reject(remote_ip, "413", 413, "Request body too large")
    except TimeoutError:
        return adapter._webhook_reject(remote_ip, "408", 408, "Request Timeout")
    except Exception:
        logger.exception("[Feishu] Webhook body read failed")
        return adapter._webhook_reject(remote_ip, "400", 400, json_msg="failed to read body")


def _authentication_error(adapter: Any, headers: Any, body: bytes, payload: dict) -> Any:
    if not (adapter._verification_token or adapter._encrypt_key):
        return "401-auth", "Webhook authentication is not configured"
    if adapter._verification_token:
        incoming = (payload.get("header") or {}).get("token") or payload.get("token") or ""
        try:
            valid = isinstance(incoming, str) and hmac.compare_digest(
                incoming.encode(), adapter._verification_token.encode(),
            )
        except UnicodeError:
            valid = False
        if not valid:
            return "401-token", "Invalid verification token"
    is_challenge = payload.get("type") == "url_verification"
    signature_supplied = any(name in headers for name in (
        "x-lark-signature", "x-lark-request-timestamp", "x-lark-request-nonce",
    ))
    # Feishu's unsigned setup challenge requires the configured token. Key-only
    # installations authenticate challenges with the same raw-body signature as events.
    require_signature = not is_challenge or not adapter._verification_token or signature_supplied
    if adapter._encrypt_key and require_signature and not adapter._is_webhook_signature_valid(headers, body):
        return "401-sig", "Invalid signature"
    return None


async def handle_webhook_request(adapter: Any, request: Any) -> Any:
    remote_ip = getattr(request, "remote", None) or "unknown"
    active = adapter._webhook_active_reads
    # Shared-ingress profiles have distinct adapters. Reserve process capacity
    # too; the short lock also covers independent listener event-loop threads.
    with _PROCESS_CAPACITY.lock:
        if active.get(remote_ip, 0) >= _MAX_ACTIVE_PER_PEER or _PROCESS_CAPACITY.active >= _MAX_ACTIVE_REQUESTS:
            return feishu.web.Response(status=503, text="Webhook capacity exhausted")
        active[remote_ip] = active.get(remote_ip, 0) + 1
        _PROCESS_CAPACITY.active += 1
    try:
        return await _handle_admitted_request(adapter, request)
    finally:
        with _PROCESS_CAPACITY.lock:
            _PROCESS_CAPACITY.active -= 1
            remaining = active[remote_ip] - 1
            if remaining:
                active[remote_ip] = remaining
            else:
                del active[remote_ip]


async def _handle_admitted_request(adapter: Any, request: Any) -> Any:
    remote_ip = getattr(request, "remote", None) or "unknown"
    # A failed peer budget cannot gate this bounded read: authentication needs
    # the raw body, and valid callbacks may share a proxy/egress with invalid ones.
    body = await _read_body(adapter, request, remote_ip)
    if not isinstance(body, bytes):
        return body
    try:
        payload = _parse_payload(body)
    except (ValueError, UnicodeError, RecursionError):
        return adapter._webhook_reject(remote_ip, "400", 400, json_msg="invalid json envelope")
    error = _authentication_error(adapter, getattr(request, "headers", {}) or {}, body, payload)
    if error:
        anomaly, text = error
        return adapter._webhook_reject(remote_ip, anomaly, 401, text)
    if payload.get("type") == "url_verification":
        return feishu.web.json_response({"challenge": payload["challenge"]})
    if payload.get("encrypt"):
        return adapter._webhook_reject(
            remote_ip, "400-encrypted", 400, json_msg="encrypted webhook payloads are not supported",
        )
    rate_key = f"{adapter._app_id}:{adapter._webhook_path}:{remote_ip}"
    if not adapter._check_webhook_rate_limit(rate_key):
        return adapter._webhook_reject(remote_ip, "429", 429, "Too Many Requests")
    adapter._clear_webhook_anomaly(remote_ip)
    event_type = str((payload.get("header") or {}).get("event_type") or "")
    data = adapter._namespace_from_mapping(payload)
    if event_type in {"im.message.reaction.created_v1", "im.message.reaction.deleted_v1"}:
        adapter._on_reaction_event(event_type, data)
    else:
        handler = adapter._WEBHOOK_EVENT_HANDLERS.get(event_type)
        if handler is None:
            feishu.logger.debug("[Feishu] Ignoring webhook event type: %s", event_type or "unknown")
        else:
            getattr(adapter, handler)(data)
    return feishu.web.json_response({"code": 0, "msg": "ok"})
