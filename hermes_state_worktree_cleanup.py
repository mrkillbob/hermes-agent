"""Durable cleanup intent for one retained conversation root, never a work queue.

The admission writer and cleanup reservation share SQLite's transaction boundary.
Git runs after that short transaction. A lost owner or uncertain removal leaves
the intent in place for explicit reconciliation, without retiring session history.
"""

import json
import uuid
from dataclasses import asdict

from hermes_state_runtime import RuntimeStoreError, _epoch, _json

INTENT_PREFIX = "gateway.worktree_cleanup.v1."


def _root_in(db, conn, sid):
    seen = set()
    while sid and sid not in seen:
        seen.add(sid)
        row = conn.execute(
            "SELECT root_session_id FROM conversation_worktree_bindings "
            "WHERE root_session_id=?",
            (sid,),
        ).fetchone()
        if row is not None:
            return sid
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if row is None or db._is_explicit_fork_child_row(dict(row)):
            return None
        sid = row["parent_session_id"]
    if sid:
        raise RuntimeStoreError("storage_unavailable")
    return None


def roots_in(db, conn, sid):
    from hermes_state_runtime_workers import runtime_lineage

    candidates = runtime_lineage(conn, sid)
    # Older native receipts use a logical chat whose physical rows alone carry
    # the worktree root. Never confuse that chat id with another profile's DB.
    candidates.update(
        row[0]
        for row in conn.execute("SELECT id FROM sessions WHERE chat_id=?", (sid,))
    )
    return {
        root
        for target in candidates
        if (root := _root_in(db, conn, target)) is not None
    }


def require_available(db, conn, sid):
    for root in roots_in(db, conn, sid):
        binding = db._conversation_worktree_record_on_conn(conn, root)
        from agent.conversation_worktree_retention import retention_pending
        from pathlib import Path
        if retention_pending(Path(binding.repo_common_dir), Path(binding.worktree_path)):
            raise RuntimeStoreError("worktree_cleanup_pending" if binding.state != "retained" else "worktree_retained")
        if binding.state == "retained":
            raise RuntimeStoreError("worktree_retained")
        if binding.state == "removed":
            raise RuntimeStoreError("worktree_removed")
        if conn.execute(
            "SELECT 1 FROM state_meta WHERE key=?", (INTENT_PREFIX + root,)
        ).fetchone():
            raise RuntimeStoreError("worktree_cleanup_pending")


def begin_cleanup(db, *, epoch, root):
    token = uuid.uuid4().hex

    def write(conn):
        _epoch(conn, epoch)
        binding = db._conversation_worktree_record_on_conn(conn, root)
        if binding is None:
            raise RuntimeStoreError("not_found")
        from agent.conversation_worktree_retention import retention_pending
        from pathlib import Path
        if retention_pending(Path(binding.repo_common_dir), Path(binding.worktree_path)):
            raise RuntimeStoreError("worktree_cleanup_pending")
        if conn.execute(
            "SELECT 1 FROM state_meta WHERE key=?", (INTENT_PREFIX + root,)
        ).fetchone():
            raise RuntimeStoreError("worktree_cleanup_pending")
        if binding.state not in {"ready", "retained", "creation_failed"}:
            raise RuntimeStoreError(
                "worktree_removed" if binding.state == "removed" else "session_busy"
            )
        pending = conn.execute(
            "SELECT target_session_id,status FROM session_admissions WHERE status IN ('queued','started','unknown') "
            "OR (status='terminal' AND outcome='steering') "
            "UNION SELECT session_id,status FROM worker_executions WHERE status IN ('registered','running','unknown')"
        ).fetchall()
        states = {row[1] for row in pending if root in roots_in(db, conn, row[0])}
        if states:
            raise RuntimeStoreError(
                "unknown_execution" if "unknown" in states else "session_busy"
            )
        intent = _json({"token": token, "epoch": epoch, "binding": asdict(binding)})
        conn.execute(
            "INSERT INTO state_meta(key,value) VALUES(?,?)",
            (INTENT_PREFIX + root, intent),
        )
        return intent

    return db._execute_write(write)


def release_refused_cleanup(db, *, epoch, root, intent):
    """Only a proved pre-mutation refusal releases this exact owner's intent."""

    def write(conn):
        _epoch(conn, epoch)
        saved = conn.execute(
            "SELECT value FROM state_meta WHERE key=?", (INTENT_PREFIX + root,)
        ).fetchone()
        if saved is None or saved[0] != intent:
            raise RuntimeStoreError("worktree_cleanup_pending")
        # Parsing also refuses an accidentally corrupted local receipt; never
        # let a token alone erase a different root's preservation evidence.
        receipt = json.loads(intent)
        if receipt["binding"]["root_session_id"] != root or receipt["epoch"] != epoch:
            raise RuntimeStoreError("storage_unavailable")
        conn.execute("DELETE FROM state_meta WHERE key=?", (INTENT_PREFIX + root,))

    db._execute_write(write)


def reserve_retention(db, expected, *, intent=None):
    """Reuse the root admission fence for privileged legacy manager callers too."""
    token = uuid.uuid4().hex

    def write(conn):
        binding = db._conversation_worktree_record_on_conn(conn, expected.root_session_id)
        if binding != expected:
            raise RuntimeStoreError("worktree_cleanup_pending")
        key = INTENT_PREFIX + expected.root_session_id
        saved = conn.execute("SELECT value FROM state_meta WHERE key=?", (key,)).fetchone()
        if saved:
            if intent is None or saved[0] != intent or json.loads(saved[0]).get("binding") != asdict(expected):
                raise RuntimeStoreError("worktree_cleanup_pending")
            return saved[0]
        if intent is not None:
            raise RuntimeStoreError("worktree_cleanup_pending")
        pending = conn.execute(
            "SELECT target_session_id FROM session_admissions WHERE status IN ('queued','started','unknown') "
            "OR (status='terminal' AND outcome='steering') UNION "
            "SELECT session_id FROM worker_executions WHERE status IN ('registered','running','unknown')"
        ).fetchall()
        if any(expected.root_session_id in roots_in(db, conn, row[0]) for row in pending):
            raise RuntimeStoreError("session_busy")
        created = _json({"token": token, "epoch": None, "binding": asdict(binding), "operation": "retention"})
        conn.execute("INSERT INTO state_meta(key,value) VALUES(?,?)", (key, created))
        return created

    return db._execute_write(write)
