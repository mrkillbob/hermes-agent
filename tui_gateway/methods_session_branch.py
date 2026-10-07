"""Branch (session.branch / session.branch_whole) helpers (``methods_session`` split).

Moved verbatim from ``tui_gateway/methods_session.py`` (file-line ratchet, #66663 CI repair):
the bodies close over server.py globals through ``method_ctx.bind_module`` exactly as before —
publication still runs from the parent's ``register()``.
"""

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()

# ── session.branch ───────────────────────────────────────────────────
def _visible_branch_history(messages) -> list:
    """user/assistant rows with visible text, as FULL copies (reasoning + timeline-marker tags survive)."""
    return [dict(message) for message in messages or []
            if isinstance(message, dict) and message.get("role") in {"user", "assistant"}
            and _coerce_message_text(message.get("content")).strip()]


def _build_branch_agent(session: dict, new_sid: str, new_key: str, history: list, source: str,
                        *, conversation_worktree=None, conversation_root_lease=None):
    """Build + register the branched agent in the parent's profile; the DEDICATED db handle is ours until
    ``_transfer_db_to_agent`` (released here on failure)."""
    parent_home = session.get("profile_home")
    branch_cwd = (conversation_worktree or {}).get("path") or _session_cwd(session)
    branch_db, branch_owns_db = _profile_session_db(parent_home) if parent_home else (None, False)
    try:
        with _profile_build_scope(parent_home):
            agent = _make_agent_in_context(new_sid, new_key, session_db=branch_db, platform_override=source,
                                           cwd_override=branch_cwd,
                                           auth_user_id=_session_auth_user_id(session),
                                           context_cwd_is_launch_artifact=(
                                               False if conversation_worktree
                                               else _context_cwd_is_launch_artifact(session)),
                                           conversation_worktree=conversation_worktree)
            _init_session(new_sid, new_key, agent, list(history), cols=session.get("cols", 80),
                          cwd=branch_cwd, session_db=branch_db, source=source, profile_home=parent_home,
                          explicit_cwd=bool(conversation_worktree or session.get("explicit_cwd")),
                          conversation_worktree=conversation_worktree,
                          conversation_root_lease=conversation_root_lease)
            _transfer_db_to_agent(agent, branch_db)
            branch_owns_db = False
        if new_sid in _sessions:
            _sessions[new_sid]["active_session_lease"] = None  # claimed lazily on the first turn
            _sessions[new_sid]["auth_user_id"] = _session_auth_user_id(session)
            # The parent's STORED key: the idempotent-hit reply for a retried
            # session.branch answers the same ``parent`` as the fresh path, and
            # later readers (lineage, retry) get the linkage from the runtime.
            _sessions[new_sid]["parent_session_id"] = session.get("session_key")
        return agent
    finally:
        if branch_owns_db and branch_db is not None:
            _release_db(branch_db)


_BRANCH_COPY_FIELDS = (
    "reasoning", "reasoning_content", "reasoning_details", "codex_reasoning_items", "codex_message_items",
    # Timeline markers ride as role=user; untagged they become bare user turns after a restart, corrupting
    # the truncate ordinal address space.
    "display_kind", "display_metadata",
    # Branch copies are history, not new activity: keep the parent's timestamps.
    "timestamp")


def _branch_source_history(db, session: dict, old_key: str) -> list:
    """Rows a branch copies: the persisted DISPLAY projection reconciled with live memory (live history is
    the MODEL projection — post-compaction summary + tail — the child would lose every archived turn)."""
    with session["history_lock"]:
        in_memory_history = [
            dict(msg) for msg in list(session.get("display_history_prefix") or []) + list(session.get("history", []))
            if isinstance(msg, dict)]
    history = None
    if callable(get_resume_conversations := getattr(db, "get_resume_conversations", None)):
        try:
            _, display_history = get_resume_conversations(old_key)
            history = _visible_branch_history(_reconcile_display_with_live(display_history, in_memory_history))
        except Exception:
            logger.debug("branch display projection read failed", exc_info=True)
    return history or _visible_branch_history(in_memory_history)


def _branch_live(rid, params: dict, session: dict, *, omit_messages: bool = False) -> dict:
    scope = _session_idempotency_scope(params, session.get("profile_home"),
                                      "session.branch_whole" if omit_messages else "session.branch",
                                      session["session_key"])
    owner = _session_auth_user_id(session)  # branches retain the parent's creating identity
    with _session_idempotency_serialized(scope):
        if hit := _session_idempotency_cached(scope, owner):
            return _ok(rid, _branch_idempotent_hit(*hit, omit_messages))
        created = []
        try:
            response = _branch_live_once(rid, params, session, omit_messages=omit_messages, on_created=created.append)
            _session_idempotency_publish(scope, response, owner)
            return response
        except BaseException:
            for sid in created:
                with contextlib.suppress(Exception):
                    _close_session_by_id(sid, end_reason="branch_create_failed")
            raise


def _branch_live_once(rid, params: dict, session: dict, *, omit_messages: bool = False, on_created=None) -> dict:
    # Write into the parent's profile-scoped state.db; the launch handle would orphan rows.
    with _session_db(session) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5008)
        old_key = session["session_key"]
        history = _branch_source_history(db, session, old_key)
        if not history:
            return _err(rid, 4008, "nothing to branch — send a message first")
        if isinstance(count := params.get("count"), int) and count > 0:
            history = history[:count]
        new_key, new_sid, source = _new_session_key(), uuid.uuid4().hex[:8], _session_source(session)
        conversation_worktree, conversation_root_lease = {}, None
        try:
            if source in {"desktop", "tui"}:
                binding = _bind_conversation_worktree_for_new_root(
                    new_key,
                    profile_home=session.get("profile_home"),
                    db=db,
                    session_cwd=_session_cwd(session),
                )
                if binding is not None:
                    conversation_worktree = _conversation_worktree_metadata(binding)
                    conversation_root_lease = _acquire_conversation_root_lease(binding, surface=source)
            title = params.get("name", "") or _branch_title(db, old_key)
            home = session.get("profile_home")
            _persist_branch(db, new_key, old_key, title, history, source=source,
                            cwd=conversation_worktree.get("path") or (
                                None if _is_remote_launch_cwd(session) else _session_cwd(session)),
                            profile_name=profile_name_for_home(home) or _current_profile_name(),
                            model=_session_default_route(session)[0], copy_fields=_BRANCH_COPY_FIELDS,
                            title_source="user" if params.get("name") else "derived",
                            user_id=_session_auth_user_id(session))
        except Exception as e:
            if conversation_root_lease is not None:
                conversation_root_lease.release()
            return _err(rid, 5008, f"branch failed: {e}")
    try:
        agent = _build_branch_agent(session, new_sid, new_key, history, source,
                                    conversation_worktree=conversation_worktree,
                                    conversation_root_lease=conversation_root_lease)
    except Exception as e:
        if not _close_session_by_id(new_sid, end_reason="branch_create_failed") and conversation_root_lease is not None:
            conversation_root_lease.release()
        return _err(rid, 5000, f"agent init failed on branch: {e}")
    if on_created is not None:
        on_created(new_sid)
    with _sessions_lock:
        if new_sid in _sessions:
            _sessions[new_sid]["branch_title"] = title
    response = {"session_id": new_sid, "stored_session_id": new_key, "title": title, "parent": old_key,
                "message_count": len(history), "info": _session_info(agent, _sessions.get(new_sid))}
    if omit_messages:
        response["messages_omitted"] = True
    else:
        response["messages"] = _history_to_messages(history, profile_home=session.get("profile_home"))
    return _ok(rid, response)


def _branch_idempotent_hit(existing_sid: str, session: dict, omit_messages: bool) -> dict:
    """The SAME result shape a fresh ``_branch_live`` returns for the existing child."""
    history = session.get("history") or []
    key = session.get("session_key") or ""
    response = {"session_id": existing_sid, "stored_session_id": key,
                "title": session.get("branch_title") or _branch_title_for(session),
                "parent": session.get("parent_session_id"), "message_count": len(history),
                "info": _fallback_session_info(session)}
    if omit_messages:
        response["messages_omitted"] = True
    else:
        response["messages"] = _history_to_messages(history, profile_home=session.get("profile_home"))
    return response


def _branch_title_for(session: dict) -> str:
    """The child's persisted title from its stored row, best-effort."""
    with contextlib.suppress(Exception):
        with _session_db(session) as db:
            if db is not None:
                return db.get_session_title(session.get("session_key") or "") or ""
    return ""

def register(server) -> None:
    """Publish this module's helpers onto ``server`` (rebound to its globals)."""
    bind_module(globals(), server, skip=("_",))
