"""Bounded inspection of verified merge evidence, never merge execution."""
import json

from .merge_controller import MergeReceipt


def recent_merge_receipts(ledger, *, repository=None):
    rows = ledger._connection.execute(
        "SELECT receipt_json FROM merge_attempts WHERE status = 'completed' "
        "AND receipt_json IS NOT NULL "
        + ("AND repository = ? " if repository is not None else "")
        + "ORDER BY updated_at DESC LIMIT 10",
        (repository,) if repository is not None else (),
    ).fetchall()
    return [MergeReceipt.from_payload(json.loads(row[0])).to_payload() for row in rows]
