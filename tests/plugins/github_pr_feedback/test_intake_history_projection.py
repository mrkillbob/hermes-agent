"""Complete legacy qualification uses the real passive host and disposable SQLite."""

from contextlib import closing
import hashlib
import io
import json
import os
import sqlite3

import pytest

from github_pr_feedback import intake_history
from github_pr_feedback.intake_context import PROTECTED_CONTEXT
from github_pr_feedback.intake_kanban import IntakeKanban
from github_pr_feedback.intake_only import IntakeError
from .support import actual_context


IDENTITY = {"repository": "mrkillbob/luna-bot", "pr_number": 7,
            "feedback_kind": "review_comment", "feedback_id": "101"}


def _fixture(tmp_path, monkeypatch, *, count=2, prose=32, status="archived"):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_CONTROL_HOME", raising=False)
    for key in PROTECTED_CONTEXT:
        monkeypatch.delenv(key, raising=False)
    context = actual_context(home)
    path = home / "kanban/boards/tradingbot-burndown/kanban.db"
    rows = []
    for index in range(count):
        receipt = (IDENTITY["repository"], 7, "review_comment", str(101 + index), "b" * 40)
        body = "private synthetic prose " + "x" * prose + "\n" + json.dumps(dict(zip(
            ("repository", "pr_number", "feedback_kind", "feedback_id", "expected_head_sha"), receipt)))
        key = "github-pr-feedback:" + hashlib.sha256(repr(receipt).encode()).hexdigest()
        rows.append((f"old-{index:05}", "legacy", body, status if index == 0 else "archived",
                     "reviewer", "scratch", key, 100))
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executemany("INSERT INTO tasks(id,title,body,status,assignee,workspace_kind,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)", rows)
        db.commit()
    adapter = IntakeKanban(lambda _: pytest.fail("CLI transport reached"),
                           expected_root=home, context=context)
    return home, path, adapter


def _change_marker_on_final_read(marker, monkeypatch):
    """Commit a real marker write after the final reader receives its bytes."""
    original_open = io.open
    marker_identity = marker.stat()
    reads = 0

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def read(self, *args, **kwargs):
            nonlocal reads
            data = self.stream.read(*args, **kwargs)
            reads += 1
            if reads == 2:
                marker.write_bytes(b'{"slug":"tradingbot-burndown","project_id":"changed"}')
            return data

    def open_file(file, mode="r", *args, **kwargs):
        stream = original_open(file, mode, *args, **kwargs)
        if mode == "rb":
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) == (marker_identity.st_dev, marker_identity.st_ino):
                return Reader(stream)
        return stream

    monkeypatch.setattr(io, "open", open_file)


@pytest.mark.parametrize("status,feedback_id", [("archived", "101"), ("done", "101"), ("archived", "999999")])
def test_complete_projected_history_preserves_cross_head_dedup(tmp_path, monkeypatch, status, feedback_id):
    home, path, adapter = _fixture(tmp_path, monkeypatch, count=2200, prose=4096, status=status)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    matches = adapter.legacy_feedback_lookup("tradingbot-burndown", {**IDENTITY, "feedback_id": feedback_id})
    assert [(row["id"], row["status"]) for row in matches] == ([("old-00000", status)] if feedback_id == "101" else [])
    assert "private synthetic prose" not in json.dumps(matches)
    assert not any("body" in row for row in matches)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert (home / "kanban/boards/tradingbot-burndown/board.json").is_file()


@pytest.mark.parametrize("fault", [
    "malformed", "unknown-key", "wrong-hash", "duplicate-json", "oversize-row",
    "missing-board", "rebound-board", "changed-history", "replaced-database", "changed-root",
    "oversize-marker", "changing-marker",
])
def test_projection_refuses_incomplete_or_changed_history(tmp_path, monkeypatch, fault):
    _home, path, adapter = _fixture(tmp_path, monkeypatch)
    marker = path.parent / "board.json"
    if fault == "oversize-marker":
        marker.write_text(json.dumps({"slug": "tradingbot-burndown", "description": "x" * 65536}))
    elif fault == "changing-marker":
        _change_marker_on_final_read(marker, monkeypatch)
    elif fault in {"malformed", "unknown-key", "wrong-hash", "duplicate-json", "oversize-row"}:
        with closing(sqlite3.connect(path)) as db:
            if fault == "unknown-key":
                db.execute("UPDATE tasks SET idempotency_key='github-pr-feedback:unknown' WHERE id='old-00001'")
            elif fault == "wrong-hash":
                db.execute("UPDATE tasks SET idempotency_key=? WHERE id='old-00001'", ("github-pr-feedback:" + "0" * 64,))
            else:
                body = {"malformed": "not a receipt", "duplicate-json": '{"repository":"a/b","repository":"a/b"}',
                        "oversize-row": "x" * (1024 * 1024 + 1)}[fault]
                db.execute("UPDATE tasks SET body=? WHERE id='old-00001'", (body,))
            db.commit()
    else:
        original = getattr(intake_history, "project_legacy_receipt", None)
        changed = False

        def change_history():
            with closing(sqlite3.connect(path)) as other:
                other.execute("UPDATE tasks SET status='done' WHERE id='old-00001'")
                other.commit()

        def replace_database():
            path.rename(path.with_suffix(".retained"))
            path.write_bytes(b"different database")

        actions = {
            "missing-board": marker.unlink,
            "rebound-board": lambda: marker.write_text(
                '{"slug":"tradingbot-burndown","project_id":"changed"}'),
            "changed-history": change_history,
            "replaced-database": replace_database,
            "changed-root": lambda: monkeypatch.setattr(
                adapter._context._manager, "home_path", tmp_path / "different-root"),
        }

        def project(task):
            nonlocal changed
            result = original(task) if original is not None else task
            if not changed:
                changed = True
                actions[fault]()
            return result

        monkeypatch.setattr(intake_history, "project_legacy_receipt", project, raising=False)
    with pytest.raises(IntakeError):
        adapter.legacy_feedback_lookup("tradingbot-burndown", {**IDENTITY, "feedback_id": "999999"})
