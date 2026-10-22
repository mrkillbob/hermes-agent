"""Explicit native operator cleanup, without adopting or executing historical sessions."""

import asyncio
from pathlib import Path

from hermes_state_runtime import RuntimeStoreError


def _root(db, sid):
    """Follow retained compression lineage, never cross an explicit fork boundary."""
    seen = set()
    while sid and sid not in seen:
        seen.add(sid)
        if db.get_conversation_worktree(sid) is not None:
            return sid
        if db.is_explicit_fork_child(sid):
            break
        row = db.get_session(sid) or {}
        sid = row.get("parent_session_id")
    return None


def _uses_root(db, sid, root):
    if _root(db, sid) == root:
        return True
    # Native admissions name their logical chat; the worktree binding belongs
    # to a physical history row (which compression may have rotated).
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT id FROM sessions WHERE chat_id=?", (sid,)
        ).fetchall()
    return any(_root(db, row[0]) == root for row in rows)


async def cleanup(connection, ref, params):
    authority, actor = connection.authority, connection.actor
    if not connection.native_owner or "session:operator" not in actor.capabilities:
        raise RuntimeStoreError("permission_denied")
    if actor.profile_id != authority.profile_id:
        raise RuntimeStoreError("profile_mismatch")
    if (
        set(params) - {"session_id", "action", "profile"}
        or not isinstance(ref.session_id, str)
        or not ref.session_id.strip()
        or not isinstance(params.get("action", "inspect"), str)
        or params.get("action", "inspect") not in {"inspect", "remove", "quarantine", "restore", "reconcile"}
        or (params.get("profile") is not None and not isinstance(params["profile"], str))
    ):
        raise RuntimeStoreError("invalid_params")
    authority._require_admission_open()
    # A live canonical mount is conservative evidence of use, including idle
    # sessions. The manager also fences other processes with its native root lease.
    live_ids = tuple(authority.sessions)
    from gateway.session_runtime_workers import track_mutation

    task = track_mutation(
        authority,
        asyncio.to_thread(
            _apply, authority, ref.session_id, params.get("action", "inspect"), live_ids
        ),
    )
    return await asyncio.shield(task)


def _apply(authority, sid, action, live_ids):
    from gateway.run import _profile_runtime_scope
    from gateway.session import _default_conversation_worktree_manager_factory
    from agent.conversation_worktree import ConversationWorktreeError

    db = authority.db
    epoch = authority.epoch
    root = _root(db, sid)
    if root is None:
        raise RuntimeStoreError("not_found")
    # Read policy under the owning home even for a single-profile gateway. The
    # manager uses the binding's immutable source, not the session's mutable cwd.
    with _profile_runtime_scope(Path(authority.profile_id)):
        try:
            manager = _default_conversation_worktree_manager_factory(db)
            if manager is None:
                raise RuntimeStoreError("worktree_isolation_disabled")
            active = any(_uses_root(db, live, root) for live in live_ids)
            # Unstarted and uncertain admissions must keep their workspace too.
            with db._read_ctx() as conn:
                pending = conn.execute(
                    "SELECT DISTINCT target_session_id FROM session_admissions "
                    "WHERE status IN ('queued','started','unknown')"
                ).fetchall()
            active = active or any(_uses_root(db, row[0], root) for row in pending)
            result = None
            if action in {"remove", "quarantine", "restore", "reconcile"}:
                from hermes_state_worktree_cleanup import (
                    begin_cleanup,
                    release_refused_cleanup,
                )

                record = db.get_conversation_worktree(root)
                intent = None
                if action == "quarantine" and not active and record is not None and record.state not in {"removed", "retained"}:
                    intent = begin_cleanup(db, epoch=epoch, root=root)
                operation = getattr(manager, {
                    "remove": "remove_after_explicit_request",
                    "quarantine": "quarantine_after_explicit_request",
                    "restore": "restore_after_explicit_request",
                    "reconcile": "reconcile_retention",
                }[action])
                result = operation(root, active_session_bound=active,
                                   **({"cleanup_intent": intent} if action == "quarantine" else {}))
                if (
                    intent is not None
                    and result.released_cleanup_intent != intent
                    and not result.removed
                    and not result.verdict.allowed
                    and result.failure_phase is None
                    and "unknown" not in result.verdict.reasons
                ):
                    release_refused_cleanup(db, epoch=epoch, root=root, intent=intent)
                verdict, removed = result.verdict, result.removed
                failure_phase, failure_message = (
                    result.failure_phase,
                    result.failure_message,
                )
            else:
                result = manager.inspect_retention(root)
                if result.outcome == "refused":
                    result = None
                verdict = manager.inspect_cleanup(root, active_session_bound=active)
                removed, failure_phase, failure_message = False, None, None
                from hermes_state_worktree_cleanup import INTENT_PREFIX

                if db._read_one(
                    "SELECT 1 FROM state_meta WHERE key=?", (INTENT_PREFIX + root,)
                ):
                    record = db.get_conversation_worktree(root)
                    if record is not None and record.state != "removed":
                        from agent.conversation_worktree import CleanupVerdict

                        verdict = CleanupVerdict(
                            False, tuple(dict.fromkeys((*verdict.reasons, "unknown")))
                        )
                        failure_phase = "cleanup"
                        failure_message = "Prior cleanup intent requires operator reconciliation; no removal retried."
        except ConversationWorktreeError as exc:
            raise RuntimeStoreError(
                "worktree_cleanup_unavailable", detail=str(exc)
            ) from exc
    if result is not None and result.outcome in {"retained", "unknown"}:
        verdict, removed = result.verdict, result.removed
        failure_phase, failure_message = result.failure_phase, result.failure_message
    record = db.get_conversation_worktree(root)
    if record is None:
        raise RuntimeStoreError("storage_unavailable")
    return {
        "allowed": verdict.allowed,
        "reasons": list(verdict.reasons),
        "removed": removed,
        "outcome": result.outcome if result is not None else "inspected",
        "retained_path": result.retained_path if result is not None else None,
        "recovery_required": result.recovery_required if result is not None else False,
        "root_session_id": record.root_session_id,
        "path": record.worktree_path,
        "branch": record.branch,
        "base_commit": record.base_commit,
        "state": record.state,
        "failure_phase": failure_phase,
        "failure_message": failure_message,
    }
