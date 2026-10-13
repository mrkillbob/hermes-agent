"""Bounded admission and diagnostic state for Feishu webhook requests."""

import logging


def admit_webhook_request(counts: dict[str, tuple[int, float]], rate_key: str, *, now: float,
                          window_seconds: float, max_requests: int, max_keys: int,
                          log: logging.Logger) -> bool:
    """Admit within a fixed window; never evict active keys to admit an untracked peer."""
    entry = counts.get(rate_key)
    if entry is not None:
        count, window_start = entry
        if now - window_start < window_seconds:
            if count >= max_requests:
                return False
            counts[rate_key] = (count + 1, window_start)
            return True
    if len(counts) >= max_keys:
        for key in [key for key, (_, start) in counts.items() if now - start >= window_seconds]:
            del counts[key]
        if rate_key not in counts and len(counts) >= max_keys:
            log.warning(
                "[Feishu] Webhook rate-limit table at capacity (%d keys) — denying untracked key",
                max_keys,
            )
            return False
    counts[rate_key] = (1, now)
    return True


def record_webhook_anomaly(counts: dict[str, tuple[int, str, float]], remote_ip: str, status: str, *,
                           now: float, ttl_seconds: float, threshold: int, max_keys: int,
                           log: logging.Logger) -> None:
    """Keep tracked-peer diagnostics while dropping new diagnostic keys at capacity."""
    entry = counts.get(remote_ip)
    if entry is None and len(counts) >= max_keys:
        for key in [key for key, (_, _, first_seen) in counts.items() if now - first_seen >= ttl_seconds]:
            del counts[key]
        if len(counts) >= max_keys:
            return
    count, _last_status, first_seen = entry or (0, "", now)
    if count and now - first_seen >= ttl_seconds:
        count, first_seen = 0, now
    count += 1
    if count % threshold == 0:
        log.warning(
            "[Feishu] Webhook anomaly: %d consecutive error responses (%s) from %s over the last %.0fs",
            count, status, remote_ip, now - first_seen,
        )
    counts[remote_ip] = (count, status, first_seen)
