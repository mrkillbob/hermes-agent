"""Title-keyed local session selection, resolved read-only by the authority.

``hermes chat --resume <title>`` / ``-c <title> [--create-if-missing]`` used to open
SessionDB in the client; the zero-writer client now sends the name and the owner
applies the classic CLI rule (``hermes_cli.main._resolve_session_by_name_or_id``).
"""
from __future__ import annotations

from hermes_state_runtime import RuntimeStoreError


def eligible_breadcrumb(authority, session_id):
    """Reject only a proven empty implicit hint; uncertain lineage remains resumable."""
    import json
    from hermes_state_common import _NOT_A_LAUNCH_STUB_SQL
    from hermes_state_local import POLICY_PREFIX
    from hermes_state_local_lineage import validate_local_lineage
    db = authority.db
    with db._read_ctx() as conn:
        # One SQLite snapshot covers receipt validation, reset boundaries and admissions.
        owns_snapshot = not conn.in_transaction
        if owns_snapshot:
            conn.execute('BEGIN')
        try:
            saved = conn.execute('SELECT value FROM state_meta WHERE key=?',
                                 (POLICY_PREFIX + session_id,)).fetchone()
            if saved is None:
                return True  # Legacy or missing proof of the complete lineage.
            try:
                receipt = json.loads(saved[0])
                if receipt['session_id'] != session_id:
                    return True
                validated_tip = validate_local_lineage(conn, receipt)
                ids = receipt.get('lineage', [validated_tip])
            except (RuntimeStoreError, KeyError, TypeError, ValueError):
                return True  # A malformed or unverified receipt cannot prove emptiness.
            start = 0
            for index, sid in enumerate(ids[:-1]):
                row = conn.execute('SELECT end_reason FROM sessions WHERE id=?', (sid,)).fetchone()
                if row and row[0] == 'session_reset':
                    start = index + 1
            has_admissions = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_admissions'"
            ).fetchone() is not None
            if not has_admissions:
                return True  # A legacy store cannot prove that no admission exists.
            predicate = _NOT_A_LAUNCH_STUB_SQL
            if any(conn.execute(
                f'SELECT 1 FROM sessions s WHERE s.id=? AND {predicate} LIMIT 1', (sid,)
            ).fetchone() is not None for sid in ids[start:]):
                return True
            # Logical FIFO admissions survive reset. Old terminal rows have no universal reset
            # watermark, so their presence is ambiguous and must preserve the hint too.
            if has_admissions and conn.execute(
                'SELECT 1 FROM session_admissions WHERE target_session_id=? LIMIT 1',
                (session_id,),
            ).fetchone() is not None:
                return True
            return False
        finally:
            if owns_snapshot:
                conn.rollback()


def resolve_titled_session(authority, actor, name, *, missing_ok=False):
    """Exact session id first, then title (latest ``"<title> #N"`` continuation wins over
    the bare title), projected forward to the compression tip. Only a local session the
    actor may read is returned, or one a messaging lane took over from a local surface
    (``handed_off_ref``); a foreign or other non-local match is ``not_found`` so another
    principal's titles are never confirmed."""
    if not isinstance(name, str) or not name.strip():
        raise RuntimeStoreError('invalid_params')
    db = authority.db
    row = db.get_session(name)
    sid = row['id'] if row else db.resolve_session_by_title(name)
    tip = db.get_session(db.get_compression_tip(sid) or sid) if sid else None
    if tip is None:
        if missing_ok:
            return None
        raise RuntimeStoreError('not_found')
    from gateway.session_route_recovery import handed_off_ref
    moved = handed_off_ref(authority, tip['id'])
    try:
        if moved is not None:
            authority.authorize(actor, moved, 'session:read')
            return moved
        from gateway.session_local_migration import resolve_local_target
        ref = resolve_local_target(authority, actor, tip['id'])
    except RuntimeStoreError as exc:
        if exc.reason == 'permission_denied':
            raise RuntimeStoreError('not_found') from None
        raise
    return ref


def resolve_latest_session(authority, actor, family, workspace=None):
    """The most recently active session of a surface family (``cli`` = classic CLI incl. its finite
    one-shot runs, ``tui``), scoped to ``workspace`` (git root / cwd) first and then global: the
    classic ``hermes -c`` / ``--resume latest`` rule (``hermes_cli.main._resolve_last_session``),
    applied by the owner so the client opens no database. Only a local session the actor may read
    counts; another principal's sessions are skipped, never confirmed."""
    from agent.session_source import CLI_FAMILY_SOURCES
    families = {'cli': sorted(CLI_FAMILY_SOURCES), 'tui': ['tui']}
    if family not in families or (workspace is not None and (not isinstance(workspace, str) or not workspace)):
        raise RuntimeStoreError('invalid_params')
    from gateway.session_local_migration import resolve_local_target
    # The owner's own rows are stored ``local``, with the family in ``created_source``.
    sources = [*families[family], 'local']
    for scope in ([workspace] if workspace else []) + [None]:
        # A launch stub (the CLI crashed or a resume failed before the first turn) is the newest row
        # and carries the surface from its insert. It has no messages and no admission, so it is never
        # "latest"; a session whose first prompt is admitted but not yet written still is.
        for row in authority.db.search_sessions(source=sources, local_created_sources=[family], limit=50,
                                                workspace_key=scope, skip_launch_stubs=True):
            tip = authority.db.get_compression_tip(row['id']) or row['id']
            try:
                return resolve_local_target(authority, actor, tip)
            except RuntimeStoreError as exc:
                if exc.reason not in {'permission_denied', 'not_found', 'profile_mismatch'}:
                    raise
    raise RuntimeStoreError('not_found')


def validate_title(title):
    from hermes_state import SessionDB
    try:
        cleaned = SessionDB.sanitize_title(title) if isinstance(title, str) else None
    except ValueError:
        cleaned = None
    if cleaned is None:
        raise RuntimeStoreError('invalid_params')
    return cleaned


def title_new_session(authority, ref, title):
    """User-provenance title on a just-created local session (auto-titling never overwrites it).
    A concurrent creator that won the unique title is a conflict, not a silent orphan."""
    try:
        authority.db.set_session_title(ref.session_id, title)
    except ValueError:
        raise RuntimeStoreError('admission_conflict') from None
