"""Truthful response projection for explicit worktree preservation operations."""

def cleanup_status(
    verdict,
    record,
    *,
    removed: bool,
    outcome: str = "inspected",
    retained_path: str | None = None,
    recovery_required: bool = False,
    failure_phase: str | None = None,
    failure_message: str | None = None,
) -> dict:
    """Project a cleanup decision into the stable JSON-RPC response shape."""
    result = {
        "allowed": bool(verdict.allowed),
        "reasons": list(verdict.reasons),
        "removed": bool(removed),
        "outcome": outcome,
        "retained_path": retained_path,
        "recovery_required": recovery_required,
        "root_session_id": str(record.root_session_id),
        "path": str(record.worktree_path),
        "branch": str(record.branch),
        "base_commit": str(record.base_commit),
        "state": str(record.state),
    }
    if failure_phase is not None:
        result["failure_phase"] = str(failure_phase)
    if failure_message is not None:
        result["failure_message"] = str(failure_message)
    return result
