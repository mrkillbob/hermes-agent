"""A reviewed binding correction cannot silently adopt a newer task/refusal."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb, kanban_transfer as transfer
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_task_binding import BindingExpectation
from tests.hermes_cli.test_kanban_task_binding import binding_case, _correct


@dataclass(frozen=True)
class ReviewedExpectation(BindingExpectation):
    revision: str = ""


def _review(case, *, board="alpha", task_id=None):
    with connect_closing(board="alpha") as conn:
        rows = transfer._selected_task_rows(conn, [case[4]])
    material = {"board": board, "task_id": task_id or case[4], "rows": rows}
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode("utf-8")
    return ReviewedExpectation.from_task(
        case[5], project_root=str(case[1][0]),
    ), hashlib.sha256(encoded).hexdigest()


def _expected(case, **scope):
    expectation, revision = _review(case, **scope)
    return ReviewedExpectation(expectation.values, expectation.project_root,
                               expectation.board_project_id, revision)


@pytest.mark.parametrize("drift", [
    "missing_review", "body", "title", "idempotency_key", "assignee", "refusal_payload",
    "empty_idempotency_key", "board_scope", "task_scope", "snapshot_refusal",
])
def test_stale_review_refuses_before_any_recovery_or_copy(request, monkeypatch, drift):
    case = request.getfixturevalue("binding_case")
    task_id = case[4]
    with connect_closing(board="alpha") as conn, kb.write_txn(conn):
        kb._append_event(conn, task_id, "privacy_denial", {
            "receipt_ref": "fixture-receipt", "task_revision_binding": "unavailable",
        })
    expected = _expected(case)
    if drift == "missing_review":
        expected = BindingExpectation.from_task(case[5], project_root=str(case[1][0]))
    elif drift in {"board_scope", "task_scope"}:
        scope = {"board": "beta"} if drift == "board_scope" else {"task_id": "other-fixture-task"}
        expected = _expected(case, **scope)
    elif drift == "snapshot_refusal":
        select = transfer._selected_task_rows
        injected = False

        def new_refusal_while_sampling(conn, ids):
            nonlocal injected
            select(conn, ids)
            if not injected:
                injected = True
                with kb.write_txn(conn):
                    kb._append_event(conn, task_id, "privacy_denial", {"receipt_ref": "later-fixture-receipt"})
            return select(conn, ids)

        monkeypatch.setattr(transfer, "_selected_task_rows", new_refusal_while_sampling)
    else:
        with connect_closing(board="alpha") as conn, kb.write_txn(conn):
            if drift == "refusal_payload":
                conn.execute("UPDATE task_events SET payload=? WHERE task_id=? AND kind='privacy_denial'",
                             (json.dumps({"receipt_ref": "different-fixture-receipt", "task_revision_binding": "unavailable"}), task_id))
            else:
                column, value = ("idempotency_key", "") if drift == "empty_idempotency_key" else (drift, "changed fixture value")
                conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, task_id))
    with pytest.raises(ValueError, match="revision"):
        _correct(case, expected=expected)
    assert not (case[0] / "before.tar.gz").exists()
    assert not (case[0] / "before.tar.gz.source.sqlite3").exists()
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, task_id).status == "triage"
    with connect_closing(board="beta") as conn:
        assert kb.get_task(conn, task_id) is None


def test_exact_review_preserves_unverified_refusal_and_hold_metadata(request):
    case = request.getfixturevalue("binding_case")
    task_id = case[4]
    payload = {"receipt_ref": "fixture-receipt", "task_revision_binding": "unavailable",
               "lineage": "unavailable", "local_privacy_denial": True}
    with connect_closing(board="alpha") as conn, kb.write_txn(conn):
        kb._append_event(conn, task_id, "privacy_denial", payload)
    result = _correct(case, expected=_expected(case))
    assert Path(result["recovery_backup"]).is_file()
    with connect_closing(board="beta") as conn:
        task = kb.get_task(conn, task_id)
        assert task.status == "triage"
        assert task.consecutive_failures == case[5]["consecutive_failures"]
        event = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='privacy_denial'", (task_id,)).fetchone()
        assert json.loads(event[0]) == payload
