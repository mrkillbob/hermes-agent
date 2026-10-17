"""Source-bound temporary SQLite bridge; no CLI process or live state access."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace

from github_pr_feedback.intake_kanban import IntakeKanban
from github_pr_feedback.intake_only import IntakeError, scan_intake
from .test_intake_legacy import legacy_database
from .test_intake_only import NOW, GitHub, policy
from .support import actual_context


def test_adapter_engine_and_passive_host_qualified_history_contract(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb

    home = tmp_path / "hermes"
    board = home / "kanban" / "boards" / "tradingbot-burndown"
    board.mkdir(parents=True)
    (board / "board.json").write_text('{"slug":"tradingbot-burndown"}', encoding="utf-8")
    dbpath = board / "kanban.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    with closing(sqlite3.connect(dbpath)) as db:
        db.executescript(kb.SCHEMA_SQL)
        db.execute("ALTER TABLE tasks ADD COLUMN completion_contract TEXT")
        db.commit()
    calls = []

    def runner(argv):
        calls.append(argv)
        if "create" in argv:
            # Actual production create/recovery functions, schema fixture only.
            # Native CLI/bootstrap behavior is a separate qualification.
            resolved = kb.kanban_db_path(board=argv[3]).resolve(strict=True)
            assert resolved == dbpath.resolve()
            with closing(sqlite3.connect(resolved, isolation_level=None)) as db:
                db.row_factory = sqlite3.Row
                task_id = kb.create_task(
                    db,
                    title=argv[argv.index("create") + 1],
                    body=argv[argv.index("--body") + 1],
                    assignee=argv[argv.index("--assignee") + 1],
                    workspace_kind="dir",
                    workspace_path=argv[argv.index("--workspace") + 1][4:],
                    triage=True,
                    idempotency_key=argv[argv.index("--idempotency-key") + 1],
                )
                kb.recompute_ready(db)
                row = dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
                assert row["status"] == "triage"
                assert not any(
                    row[k]
                    for k in (
                        "current_run_id",
                        "worker_pid",
                        "claim_lock",
                        "model_override",
                        "provider_override",
                        "skills",
                        "goal_mode",
                    )
                )
            return {"id": task_id}
        raise AssertionError("Only triage create may use a CLI transport")

    legacy = legacy_database(tmp_path / "legacy.sqlite3")
    github = GitHub()
    adapter = IntakeKanban(runner, expected_root=home, context=actual_context(home))

    def scan():
        return scan_intake(
            policy(tmp_path),
            github,
            adapter,
            tmp_path / "private" / "receipts.sqlite3",
            now=NOW,
            legacy_path=legacy,
        )

    assert scan()["created"] == 1
    github.pr = replace(github.pr, head_sha="b" * 40)
    assert scan()["created"] == 0
    assert sum("create" in argv for argv in calls) == 1
    # Matching historical feedback on a different head, including archived,
    # fences publication even when its legacy receipt row is absent.
    key = (github.pr.base_repository, 7, "review_comment", "202", "c" * 40)
    evidence = dict(
        zip(("repository", "pr_number", "feedback_kind", "feedback_id", "expected_head_sha"), key)
    )
    legacy_key = "github-pr-feedback:" + hashlib.sha256(repr(key).encode()).hexdigest()
    with closing(sqlite3.connect(dbpath)) as db:
        db.execute(
            "INSERT INTO tasks(id,title,body,status,assignee,workspace_kind,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                "old",
                "legacy",
                json.dumps(evidence),
                "archived",
                "reviewer",
                "scratch",
                legacy_key,
                100,
            ),
        )
        db.commit()
    github.items = (replace(github.items[0], feedback_id="202"),)
    assert scan()["status"] == "held"
    assert sum("create" in argv for argv in calls) == 1
    github.items = (replace(github.items[0], feedback_id="303"),)
    with closing(sqlite3.connect(dbpath)) as db:
        db.execute("UPDATE tasks SET body='unclassifiable legacy body' WHERE id='old'")
        db.commit()
    result = scan()
    assert result["status"] == "held" and result["reason"] == "legacy_card_history_unqualified"
    assert sum("create" in argv for argv in calls) == 1


import pytest


@pytest.mark.parametrize(
    "context", ["worker_db", "false_delegate_db", "nonworker_db", "wrong_home"]
)
def test_active_pins_never_redirect_intake_into_a_different_database(
    tmp_path, monkeypatch, context
):
    from hermes_cli import kanban_db as kb

    home, other = tmp_path / "hermes", tmp_path / "other"
    target = home / "kanban" / "boards" / "tradingbot-burndown"
    other_target = other / "kanban" / "boards" / "tradingbot-burndown"
    for directory in (target, other_target):
        directory.mkdir(parents=True)
        (directory / "board.json").write_text('{"slug":"tradingbot-burndown"}', encoding="utf-8")
    paths = [home / "kanban.db", target / "kanban.db", other_target / "kanban.db"]
    for path in paths:
        with closing(sqlite3.connect(path)) as db:
            db.executescript(kb.SCHEMA_SQL)
            db.execute("ALTER TABLE tasks ADD COLUMN completion_contract TEXT")
            db.commit()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_CONTROL_HOME", raising=False)
    if context == "wrong_home":
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(other))
    else:
        monkeypatch.setenv("HERMES_KANBAN_DB", str(paths[0]))
        if context == "worker_db":
            monkeypatch.setenv("HERMES_KANBAN_TASK", "inert-worker")
        if context == "false_delegate_db":
            monkeypatch.setenv("HERMES_DELEGATED_CHILD_CONTEXT", "false")
    calls = []

    def runner(argv):
        calls.append(argv)
        if "create" in argv:
            # Crucially use the actual host resolver, not the expected DB path.
            path = kb.kanban_db_path(board=argv[3]).resolve(strict=True)
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                db.row_factory = sqlite3.Row
                task_id = kb.create_task(
                    db,
                    title=argv[argv.index("create") + 1],
                    body=argv[argv.index("--body") + 1],
                    assignee="task-intake-router",
                    workspace_kind="dir",
                    workspace_path=str(tmp_path),
                    triage=True,
                    idempotency_key=argv[argv.index("--idempotency-key") + 1],
                )
            return {"id": task_id}
        raise AssertionError("Only triage create may use a CLI transport")

    legacy = legacy_database(tmp_path / "legacy.sqlite3")
    result = scan_intake(
        policy(tmp_path),
        GitHub(),
        IntakeKanban(runner, expected_root=home, context=actual_context(home)),
        tmp_path / "private" / "receipts.sqlite3",
        now=NOW,
        legacy_path=legacy,
    )
    assert result["status"] != "ok" and result["created"] == 0
    assert calls == []
    for path in paths:
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
