"""copilot-acp picker catalog: the signed-in CLI session's models (memoized), else the HTTP
``copilot`` provider's catalog (same account id space)."""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_SESSION_MEMO_TTL = 300.0  # 5 min; SWR disk cache handles the rest
_SESSION_FAIL_TTL = 30.0  # failed probes re-probe quickly so a fresh CLI login is picked up
_session_memo: Optional[tuple[float, float, Optional[list[str]]]] = None  # (at, ttl, models)


def session_models(force_refresh: bool) -> Optional[list[str]]:
    """Enabled models from a signed-in ``copilot --acp`` session, memoized for a few minutes —
    successes AND failures. Model-switch validation (``models_validate._static_catalog``) reads
    this uncached on every ``/model`` switch, and each miss is a CLI spawn + handshake (up to the
    probe timeout), so without the memo every switch paid a subprocess. A failed probe is
    memoized much more briefly so a user who signs in to the CLI right after a miss is picked up
    on the next switch (or immediately via ``/model --refresh``, which clears this memo)."""
    global _session_memo
    now = time.monotonic()
    memo = _session_memo
    if not force_refresh and memo is not None and now - memo[0] < memo[1]:
        return memo[2]
    from providers import get_provider_profile

    try:
        live = get_provider_profile("copilot-acp").fetch_models() or None
    except Exception:
        logger.debug("copilot-acp session model discovery failed", exc_info=True)
        live = None
    _session_memo = (now, _SESSION_MEMO_TTL if live else _SESSION_FAIL_TTL, live)
    return live


def clear_session_memo() -> None:
    """A fresh CLI login must show on the next ``/model`` switch: ``--refresh`` drops the memo
    (and its failure memo) so it never serves a stale signed-out probe."""
    global _session_memo
    _session_memo = None


def acp_picker_catalog(force_refresh: bool = False) -> Optional[list[str]]:
    if live := session_models(force_refresh):
        return live
    from providers import get_provider_profile

    copilot = get_provider_profile("copilot")
    if copilot is None or copilot.picker_catalog is None:
        return None
    return copilot.picker_catalog(force_refresh)
