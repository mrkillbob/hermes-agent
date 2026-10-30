"""Repository-filtered summaries without loading receipt payloads."""
_QUERIES = {
    "feedback": ("SELECT status, COUNT(*) FROM feedback_receipts ",
                 ("claimed", "completed", "failed")),
    "merge": ("SELECT status, COUNT(*) FROM merge_attempts ",
              ("claimed", "verification_required", "completed", "failed")),
}


def status_counts(connection, kind, repository):
    query, statuses = _QUERIES[kind]
    counts = dict.fromkeys(statuses, 0)
    if repository is not None:
        query += "WHERE repository = ? "
    for status, count in connection.execute(query + "GROUP BY status", (repository,) if repository is not None else ()):
        if status in counts:
            counts[status] = int(count)
    return counts
