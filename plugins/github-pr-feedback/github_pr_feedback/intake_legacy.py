"""Read-only reconciliation of the existing scanner's immutable feedback identity.

Never constructs FeedbackLedger: that constructor initializes and migrates state.
Missing or unreadable provenance is a hold, not permission to start fresh.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .intake_only import IntakeError


class LegacyReceipts:
    def __init__(self, path):
        self.path = Path(path) if path is not None else None

    def disposition(self, receipt_key):
        path = self.path
        if path is None or path.is_symlink() or not path.is_file():
            raise IntakeError("existing legacy delivery provenance unavailable")
        try:
            with closing(
                sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=3)
            ) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                version = db.execute("PRAGMA data_version").fetchone()
                if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise IntakeError("legacy delivery provenance corrupt")
                columns = {row[1] for row in db.execute("PRAGMA table_info(feedback_receipts)")}
                required = {
                    "repository",
                    "pr_number",
                    "feedback_kind",
                    "feedback_id",
                    "head_sha",
                    "status",
                    "task_id",
                }
                if not required <= columns:
                    raise IntakeError("legacy delivery provenance schema unavailable")
                rows = db.execute(
                    "SELECT head_sha,status,task_id FROM feedback_receipts "
                    "WHERE repository=? AND pr_number=? AND feedback_kind=? AND feedback_id=? LIMIT 1001",
                    receipt_key[:4],
                ).fetchall()
                if len(rows) > 1000 or db.execute("PRAGMA data_version").fetchone() != version:
                    raise IntakeError("complete stable legacy provenance unavailable")
        except (sqlite3.Error, OSError):
            raise IntakeError("legacy delivery provenance unavailable") from None
        for head, status, task_id in rows:
            if (
                not isinstance(head, str)
                or len(head) not in (40, 64)
                or any(c not in "0123456789abcdefABCDEF" for c in head)
                or status not in ("completed", "claimed", "failed")
                or (status == "completed" and (not isinstance(task_id, str) or not task_id))
            ):
                raise IntakeError("legacy delivery provenance ambiguous")
        if any(status == "completed" for _, status, _ in rows):
            return "accounted"
        return "held" if rows else "absent"
