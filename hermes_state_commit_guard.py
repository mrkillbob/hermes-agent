"""Storage placement for trusted owner leases; this module issues no authorization.

The private factory is selected only by trusted application code, not request or
note data. It receives the actual SQLite transaction after SQL and must acquire
the owner's real revocation/replacement lease at this commit boundary. A prior
observation or arbitrary allow callback cannot substitute for that lease.
"""
from contextlib import nullcontext


class CommitGuardFailure(RuntimeError):
    """Guard failure with uncertain write outcome; never a retryable SQLite error."""


def commit_transaction(connection, guard_factory=None):
    """Hold the supplied lease through the actual commit, never through DB-lock retry.

    Guard entry refusal is rolled back by SessionDB's existing exception path.
    Factories may be entered again on an ordinary SQLite lock retry. Expiry or
    revocation must raise a nonretryable refusal, not a SQLite busy exception.
    A guard must not commit, roll back or invoke DB/owner callbacks itself.
    A commit or guard-exit error may follow a durable effect: callers must retain
    that uncertain outcome, not infer rollback or automatically create new work.
    An exit hook must not run native/DB invalidation under inverted lock order.
    """
    committed = False
    try:
        with nullcontext() if guard_factory is None else guard_factory(connection):
            connection.commit()
            committed = True
    except Exception:
        if committed:
            # A release hook's SQLITE_BUSY must not make _execute_write repeat SQL
            # that already committed. Keep a bounded, non-retryable uncertainty.
            raise CommitGuardFailure('commit_guard_release_failed') from None
        raise
    if not committed:
        raise CommitGuardFailure('commit_guard_suppressed_failure')
