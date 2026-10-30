"""Cron claim-owner liveness shared across job-store upgrade generations.

Keep this leaf independent of cron.jobs: a fresh occurrence reader may run
beside the job-store module cached before an on-disk upgrade.
"""

from typing import Any


def _fire_claim_owner_is_dead(claim: Any) -> bool:
    """Return true only for a same-host claim whose recorded PID is gone."""
    if not isinstance(claim, dict):
        return False
    owner = str(claim.get("by") or "")
    host, separator, rest = owner.partition(":")
    if not separator or not rest:
        return False
    import socket
    if host != socket.gethostname():
        return False
    pid_text = rest.split(":", 1)[0]
    if not pid_text.isdigit() or int(pid_text) <= 0:
        return False
    try:
        from gateway.status import _pid_exists

        return not _pid_exists(int(pid_text))
    except (ImportError, ValueError, TypeError):
        return False
