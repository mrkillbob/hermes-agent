"""Temporary SQLite only: never instantiate the mutating legacy ledger."""

import hashlib
import sqlite3

import pytest
from .test_intake_only import NOW, GitHub, Kanban, policy


def legacy_database(path, rows=()):
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE feedback_receipts(repository TEXT, pr_number INTEGER, feedback_kind TEXT, feedback_id TEXT, head_sha TEXT, status TEXT, task_id TEXT)"
        )
        db.executemany("INSERT INTO feedback_receipts VALUES(?,?,?,?,?,?,?)", rows)
    return path


@pytest.mark.parametrize(
    "status,expected", [("completed", "accounted"), ("claimed", "held"), ("failed", "held")]
)
def test_cross_head_legacy_receipts_are_read_only(tmp_path, status, expected):
    from github_pr_feedback.intake_legacy import LegacyReceipts

    path = legacy_database(
        tmp_path / "legacy.sqlite3",
        [("mrkillbob/luna-bot", 7, "review_comment", "101", "b" * 40, status, "legacy-task")],
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert (
        LegacyReceipts(path).disposition(
            ("mrkillbob/luna-bot", 7, "review_comment", "101", "a" * 40)
        )
        == expected
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("mode", ["missing", "corrupt", "schema", "unknown"])
def test_unproven_legacy_provenance_holds(tmp_path, mode):
    from github_pr_feedback.intake_legacy import LegacyReceipts

    path = tmp_path / "legacy.sqlite3"
    if mode == "corrupt":
        path.write_bytes(b"not sqlite")
    elif mode == "schema":
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE wrong(x)")
    elif mode == "unknown":
        legacy_database(
            path,
            [("mrkillbob/luna-bot", 7, "review_comment", "101", "b" * 40, "unexpected", "task")],
        )
    with pytest.raises(Exception):
        LegacyReceipts(path).disposition(
            ("mrkillbob/luna-bot", 7, "review_comment", "101", "a" * 40)
        )


@pytest.mark.parametrize("status", ["completed", "claimed", "failed"])
def test_legacy_delivery_never_creates_a_new_card(tmp_path, status):
    from github_pr_feedback.intake_only import scan_intake

    path = legacy_database(
        tmp_path / "legacy.sqlite3",
        [("mrkillbob/luna-bot", 7, "review_comment", "101", "b" * 40, status, "legacy-task")],
    )
    kb = Kanban()
    result = scan_intake(
        policy(tmp_path),
        GitHub(),
        kb,
        tmp_path / "new" / "receipts.sqlite3",
        now=NOW,
        legacy_path=path,
    )
    assert result["status"] == ("ok" if status == "completed" else "held")
    assert kb.creates == 0


def test_archived_legacy_card_without_receipt_is_held(tmp_path):
    from github_pr_feedback.intake_only import scan_intake

    class LegacyCard(Kanban):
        def legacy_feedback_lookup(self, board, identity):
            assert identity["feedback_id"] == "101"
            return [{"id": "old", "status": "archived"}]

    path = legacy_database(tmp_path / "legacy.sqlite3")
    kb = LegacyCard()
    result = scan_intake(
        policy(tmp_path),
        GitHub(),
        kb,
        tmp_path / "new" / "receipts.sqlite3",
        now=NOW,
        legacy_path=path,
    )
    assert result["status"] == "held" and kb.creates == 0


def test_unqualified_legacy_card_history_holds_new_delivery_even_with_intact_legacy_db(tmp_path):
    from github_pr_feedback.intake_only import scan_intake

    path = legacy_database(tmp_path / "legacy.sqlite3")

    class Unqualified(Kanban):
        def legacy_feedback_lookup(self, board, identity):
            raise RuntimeError("synthetic unsupported historical source format")

    kb = Unqualified()
    result = scan_intake(
        policy(tmp_path),
        GitHub(),
        kb,
        tmp_path / "new" / "receipts.sqlite3",
        now=NOW,
        legacy_path=path,
    )
    assert result["status"] == "held" and kb.creates == 0
