"""Shared eligibility for discarding an untouched, standalone session row."""


def session_is_empty(conn, session_id):
    from hermes_state_local import owned_lineage_ids
    if owned_lineage_ids(conn, session_id) != [session_id]:
        return False
    return conn.execute(
        "SELECT 1 FROM sessions WHERE id=? AND title IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM messages WHERE messages.session_id=sessions.id)"
        " AND NOT EXISTS (SELECT 1 FROM sessions child WHERE child.parent_session_id=sessions.id)",
        (session_id,),
    ).fetchone() is not None
