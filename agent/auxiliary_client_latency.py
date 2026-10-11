"""Auxiliary-call elapsed timing and first-progress bookkeeping."""

import time
from typing import Dict, Optional


def _elapsed_ms(started_at: float, now: Optional[float] = None) -> int:
    """Whole milliseconds since ``started_at`` (clamped at 0)."""
    return max(0, int(((time.monotonic() if now is None else now) - started_at) * 1000))


def _stamp_latency_once(latency_info: Optional[dict[str, int]], key: str, started_at: float) -> None:
    """Record ``key`` in ``latency_info`` the first time it fires."""
    if latency_info is not None and key not in latency_info:
        latency_info[key] = _elapsed_ms(started_at)
