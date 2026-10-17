"""Passive intake lookup exercises the real parser/command with temporary boards."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from hermes_cli import kanban as command
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

KEY = "github-pr-intake:v1:" + "a" * 64
LEGACY_KEY = "github-pr-feedback:" + "c" * 64


def invoke(*args):
    parser = argparse.ArgumentParser()
    command.build_parser(parser.add_subparsers(dest="command"))
    return command.kanban_command(parser.parse_args(["kanban", *args]))


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    directory = home / "kanban" / "boards" / "feedback"
    directory.mkdir(parents=True)
    (directory / "board.json").write_text('{"slug":"feedback"}')
    (home / "kanban" / "current").write_text("unrelated\n")
    path = directory / "kanban.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(kb.SCHEMA_SQL)
        # Existing initialized boards receive this additive migration; the
        # passive command must require it rather than apply it itself.
        connection.execute("ALTER TABLE tasks ADD COLUMN completion_contract TEXT")
        connection.execute(
            "INSERT INTO tasks (id,title,body,status,assignee,workspace_kind,idempotency_key,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("archived-card", "inert title", "inert body", "archived", "reviewer", "scratch", KEY, 100),
        )
        connection.commit()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)

    def refuse(*_args, **_kwargs):
        pytest.fail("passive lookup called a mutator/network/process")

    for module, name in ((kb, "init_db"), (kb, "recompute_ready"),
                         (kbc, "connect_closing"), (kb, "set_current_board"),
                         (socket, "socket"), (subprocess, "Popen")):
        monkeypatch.setattr(module, name, refuse)
    return home, path


def snapshot(home):
    return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in home.rglob("*") if p.is_file()}


def lookup_args(key=KEY, board="feedback"):
    return ["--board", board, "intake-lookup", "--idempotency-key", key, "--json"]


def test_exact_lookup_includes_archived_full_data_and_changes_nothing(board, capsys):
    home, path = board
    before = snapshot(home)
    assert invoke(*lookup_args()) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    row = rows[0]
    assert (row["id"], row["title"], row["body"], row["status"]) == (
        "archived-card", "inert title", "inert body", "archived")
    assert row["assignee"] == "reviewer"
    assert row["workspace_kind"] == "scratch"
    assert row["workspace_path"] is None
    assert row["idempotency_key"] == KEY
    assert row["model_override"] is None
    assert row["provider_override"] is None
    assert row["reasoning_effort"] is None
    with sqlite3.connect(path) as connection:
        columns = {column[1] for column in connection.execute("PRAGMA table_info(tasks)")}
    assert columns == row.keys()
    assert snapshot(home) == before


def test_no_match_is_empty_without_fallback(board, capsys):
    assert invoke(*lookup_args("github-pr-intake:v1:" + "b" * 64)) == 0
    assert json.loads(capsys.readouterr().out) == []


@pytest.mark.parametrize("key", ["", "github-pr-intake:v1:" + "A" * 64,
                                  "github-pr-intake:v2:" + "a" * 64,
                                  "github-pr-intake:v1:" + "a" * 63,
                                  "github-pr-intake:v1:" + "a" * 64 + "\n",
                                  "other:v1:" + "a" * 64, "' OR 1=1 --"])
def test_invalid_namespace_rejected_before_database_open(board, monkeypatch, capsys, key):
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("invalid key opened DB"))
    assert invoke(*lookup_args(key)) != 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("args", [["intake-lookup", "--idempotency-key", KEY, "--json"],
                                  ["--board", "feedback", "intake-lookup", "--idempotency-key", KEY]])
def test_explicit_board_and_json_are_required(board, capsys, args):
    assert invoke(*args) != 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("state", ["missing", "corrupt", "wrong-schema", "missing-override"])
def test_unreadable_or_incomplete_database_fails_closed(board, capsys, state):
    home, path = board
    if state == "missing":
        path.unlink()
    elif state == "corrupt":
        path.write_bytes(b"not a sqlite database")
    elif state == "wrong-schema":
        path.unlink()
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE unrelated (id TEXT)")
    else:
        with sqlite3.connect(path) as connection:
            connection.execute("ALTER TABLE tasks DROP COLUMN model_override")
    before = snapshot(home)
    assert invoke(*lookup_args()) != 0
    assert capsys.readouterr().out == ""
    assert snapshot(home) == before


def test_missing_named_board_is_not_created(board, capsys):
    home, _ = board
    before = snapshot(home)
    assert invoke(*lookup_args(board="missing")) != 0
    assert capsys.readouterr().out == ""
    assert snapshot(home) == before


def test_symlinked_database_is_opened_by_existing_canonical_path(board, monkeypatch, capsys):
    home, path = board
    canonical = home / "real-board.db"
    path.rename(canonical)
    path.symlink_to(canonical)
    actual_connect = sqlite3.connect
    opened = []

    def connect(database, *args, **kwargs):
        opened.append((database, kwargs))
        return actual_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert invoke(*lookup_args()) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert opened == [(canonical.resolve().as_uri() + "?mode=ro", {"uri": True, "timeout": 1.0})]


def test_output_over_one_mib_is_rejected_without_partial_payload(board, capsys):
    _, path = board
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE tasks SET body=?", ("x" * (1024 * 1024),))
    assert invoke(*lookup_args()) != 0
    assert capsys.readouterr().out == ""


def test_explicit_board_keeps_existing_worker_pin_policy(board, monkeypatch, capsys):
    _, path = board
    monkeypatch.setenv("HERMES_KANBAN_DB", str(path))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "inert-owned-task")
    assert invoke(*lookup_args(board="default")) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "archived-card"


def test_live_wal_rows_are_visible_without_database_or_wal_changes(board, capsys):
    home, path = board
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE tasks SET title='committed in WAL'")
        writer.commit()
        wal = path.with_name(path.name + "-wal")
        before_db, before_wal = path.read_bytes(), wal.read_bytes()
        names = set(snapshot(home))
        assert invoke(*lookup_args()) == 0
        assert json.loads(capsys.readouterr().out)[0]["title"] == "committed in WAL"
        assert path.read_bytes() == before_db
        assert wal.read_bytes() == before_wal
        assert set(snapshot(home)) == names


def test_wal_database_without_sidecars_allows_only_sqlite_coordination_files(board, monkeypatch, capsys):
    home, path = board
    # URI punctuation must remain part of the pinned filename, never options.
    special_home = home.with_name("hermes ? # %")
    relative = path.relative_to(home)
    home.rename(special_home)
    path = special_home / relative
    monkeypatch.setenv("HERMES_HOME", str(special_home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(special_home))
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE tasks SET title='latest checkpointed commit'")
        writer.commit()
    wal = path.with_name(path.name + "-wal")
    shm = path.with_name(path.name + "-shm")
    assert not wal.exists()
    assert not shm.exists()
    before = snapshot(special_home)
    database_bytes = path.read_bytes()
    assert invoke(*lookup_args()) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert (row["id"], row["title"], row["status"], row["idempotency_key"]) == (
        "archived-card", "latest checkpointed commit", "archived", KEY)
    assert path.read_bytes() == database_bytes
    after = snapshot(special_home)
    assert all(after[name] == digest for name, digest in before.items())
    allowed = {str(wal.relative_to(special_home)), str(shm.relative_to(special_home))}
    assert set(after) - set(before) <= allowed
    if wal.exists():
        assert wal.stat().st_size == 0
    # Shared-memory bytes are SQLite's reader coordination, not task data.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as reader:
        assert reader.execute("SELECT title,status FROM tasks WHERE idempotency_key=?", (KEY,)).fetchone() == (
            "latest checkpointed commit", "archived")
    assert path.read_bytes() == database_bytes


def test_explicit_default_missing_database_never_uses_named_board(board, capsys):
    home, _ = board
    before = snapshot(home)
    assert invoke(*lookup_args(board="default")) != 0
    assert capsys.readouterr().out == ""
    assert snapshot(home) == before


def test_invalid_board_slug_rejected_before_open(board, monkeypatch, capsys):
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("invalid board opened DB"))
    assert invoke(*lookup_args(board="../other")) != 0
    assert capsys.readouterr().out == ""


def test_lookup_preserves_stored_execution_overrides_for_verification(board, capsys):
    _, path = board
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE tasks SET model_override='inert-model',provider_override='inert-provider',"
                           "reasoning_effort='high',skills='[\"inert\"]',branch_name='inert-branch'")
        connection.commit()
    assert invoke(*lookup_args()) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row["model_override"] == "inert-model"
    assert row["provider_override"] == "inert-provider"
    assert row["reasoning_effort"] == "high"
    assert row["skills"] == '["inert"]'
    assert row["branch_name"] == "inert-branch"


def test_utf8_json_bound_rejects_expansion_without_truncation(board, capsys):
    _, path = board
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE tasks SET body=?", ("\x00" * 200_000,))
        connection.commit()
    assert invoke(*lookup_args()) != 0
    assert capsys.readouterr().out == ""


def test_legacy_lookup_matches_only_exact_receipt_and_known_composed_suffixes(board, capsys):
    home, path = board
    wanted = [LEGACY_KEY, LEGACY_KEY + ":dispatch-2", LEGACY_KEY + ":dispatch-17",
              LEGACY_KEY + ":typed-fixer-v3", LEGACY_KEY + ":supervised-v4",
              LEGACY_KEY + ":typed-fixer-v3:dispatch-2",
              LEGACY_KEY + ":supervised-v4:dispatch-17"]
    excluded = ["github-pr-feedback:" + "d" * 64,
                "github-pr-feedback:intent-review:repo:17:review:99:head:digest",
                LEGACY_KEY + ":dispatch-0", LEGACY_KEY + ":dispatch-1",
                LEGACY_KEY + ":dispatch-02", LEGACY_KEY + ":dispatch-2x",
                LEGACY_KEY + ":dispatch-2\n", LEGACY_KEY + ":typed-fixer-v2",
                LEGACY_KEY + ":supervised-v4:dispatch-02",
                LEGACY_KEY + ":typed-fixer-v3:supervised-v4",
                LEGACY_KEY + ":typed-fixer-v3:dispatch-2:extra"]
    with closing(sqlite3.connect(path)) as connection:
        for number, key in enumerate(wanted + excluded):
            connection.execute(
                "INSERT INTO tasks (id,title,body,status,assignee,workspace_kind,idempotency_key,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"legacy-{number}", "inert", "inert", "archived", "legacy-owner", "dir", key, 100))
        connection.commit()
    before = snapshot(home)
    assert invoke("--board", "feedback", "intake-lookup", "--legacy-receipt-key", LEGACY_KEY, "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["idempotency_key"] for row in rows} == set(wanted)
    assert all(row["status"] == "archived" and row["assignee"] == "legacy-owner" for row in rows)
    assert snapshot(home) == before


@pytest.mark.parametrize("key", ["github-pr-feedback:key", LEGACY_KEY + ":typed-fixer-v3",
                                  LEGACY_KEY.upper(), KEY,
                                  "github-pr-feedback:intent-review:repo:17:review:99:head:digest",
                                  LEGACY_KEY + "\n"])
def test_legacy_selector_requires_exact_canonical_base_before_open(board, monkeypatch, capsys, key):
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("invalid legacy key opened DB"))
    assert invoke("--board", "feedback", "intake-lookup", "--legacy-receipt-key", key, "--json") != 0
    assert capsys.readouterr().out == ""


def test_legacy_and_new_selectors_are_mutually_exclusive(board, monkeypatch, capsys):
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("two selectors opened DB"))
    with pytest.raises(SystemExit) as error:
        invoke(*lookup_args(), "--legacy-receipt-key", LEGACY_KEY)
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_legacy_lookup_absence_is_an_empty_read_not_a_write(board, capsys):
    home, _ = board
    before = snapshot(home)
    assert invoke("--board", "feedback", "intake-lookup", "--legacy-receipt-key", LEGACY_KEY, "--json") == 0
    assert json.loads(capsys.readouterr().out) == []
    assert snapshot(home) == before


def feedback_identity(**changes):
    value = {"repository": "acme/widgets", "pr_number": 17, "feedback_kind": "review_comment", "feedback_id": "99"}
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def identity_lookup(raw=None):
    return invoke("--board", "feedback", "intake-lookup", "--legacy-feedback-identity",
                  feedback_identity() if raw is None else raw, "--json")


@pytest.fixture
def legacy_producer(monkeypatch):
    # Use the actual legacy receipt/hash/renderer, never a surrogate SQL or
    # a fake predicate that assumes the new lookup has proved completeness.
    plugin = Path(__file__).resolve().parents[2] / "plugins" / "github-pr-feedback"
    monkeypatch.syspath_prepend(str(plugin))
    from github_pr_feedback.cli import _kanban_create_argv, _kanban_reconcile_argv
    from github_pr_feedback.controller import KanbanTask, _intent_review_task, _receipt_idempotency_key
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.repair_controller import _actions_needed_task

    def produce(*, repository="acme/widgets", kind="review_comment", feedback_id="99", head="a" * 40,
                suffix="", evidence=None, intent=False, actions=False, whole_json=False, reconcile=False):
        receipt = FeedbackReceipt(repository, 17, kind, feedback_id, head)
        policy = SimpleNamespace(board="feedback", assignee="legacy-owner", merge_maintainer=None)
        if intent:
            task = _intent_review_task(policy, receipt, "inert decision", Path("/inert"))
        elif actions:
            task = _actions_needed_task(policy, receipt, Path("/inert"),
                                        SimpleNamespace(base_branch="main", head_ref_name="inert"))
        else:
            data = evidence if evidence is not None else {
                "repository": receipt.repository, "pr_number": receipt.pr_number,
                "feedback_kind": receipt.feedback_kind, "feedback_id": receipt.feedback_id,
                "expected_head_sha": receipt.head_sha,
            }
            task = KanbanTask("inert", "inert", "feedback", "legacy-owner", Path("/inert"), head,
                              "inert", _receipt_idempotency_key(receipt) + suffix, data)
        argv = _kanban_reconcile_argv("inert-task", task) if reconcile else _kanban_create_argv(task)
        body = json.dumps(task.evidence) if whole_json else argv[argv.index("--body") + 1]
        return task.idempotency_key, body

    return produce


def add_legacy_rows(path, rows):
    with closing(sqlite3.connect(path)) as connection:
        for number, (key, body) in enumerate(rows):
            connection.execute(
                "INSERT INTO tasks (id,title,body,status,assignee,workspace_kind,idempotency_key,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"identity-{number}", "inert", body, "archived", "legacy-owner", "dir", key, 100))
        connection.commit()


def test_identity_lookup_finds_old_heads_case_variants_and_intent_holds(board, capsys, legacy_producer):
    home, path = board
    rows = [legacy_producer(head="a" * 40), legacy_producer(head="b" * 40),
            legacy_producer(repository="Acme/Widgets", head="c" * 40),
            legacy_producer(head="d" * 40, intent=True),
            legacy_producer(repository="other/repository", head="e" * 40)]
    add_legacy_rows(path, rows)
    before = snapshot(home)
    assert identity_lookup() == 0
    found = json.loads(capsys.readouterr().out)
    assert {row["idempotency_key"] for row in found} == {key for key, _ in rows[:4]}
    assert all(row["status"] == "archived" for row in found)
    assert snapshot(home) == before


def test_identity_lookup_proves_absence_only_after_validating_other_rows(board, capsys, legacy_producer):
    _, path = board
    add_legacy_rows(path, [legacy_producer(feedback_id="100"), legacy_producer(repository="other/repo", whole_json=True)])
    assert identity_lookup() == 0
    assert json.loads(capsys.readouterr().out) == []


def test_identity_lookup_can_exclude_hash_bound_typed_ci_and_actions_rows(board, capsys, legacy_producer):
    _, path = board
    typed = {"repository": "acme/widgets", "pr_number": 17, "expected_head_sha": "a" * 40,
             "ci_receipt_id": "f" * 64}
    add_legacy_rows(path, [legacy_producer(kind="pr_repair", feedback_id="ci-receipt:" + "f" * 64,
                                         suffix=":typed-fixer-v3:dispatch-2", evidence=typed),
                           legacy_producer(kind="pr_actions_needed", feedback_id="github-action-required", actions=True)])
    assert identity_lookup() == 0
    assert json.loads(capsys.readouterr().out) == []


@pytest.mark.parametrize("problem", ["malformed-body", "unknown-suffix", "hash-mismatch", "duplicate-json", "supervised"])
def test_identity_lookup_unqualified_row_holds_without_partial_or_unrelated_body(board, capsys, legacy_producer, problem):
    _, path = board
    wanted = legacy_producer()
    key, body = legacy_producer(repository="other/repository")
    variants = {
        "malformed-body": lambda: (key, "UNRELATED-PRIVATE-TASK-BODY"),
        "unknown-suffix": lambda: (key + ":unknown", body),
        "hash-mismatch": lambda: (key, body.replace("other/repository", "forged/repository")),
        "duplicate-json": lambda: (key, '{"repository":"other/repository","repository":"forged/repository"}'),
        "supervised": lambda: legacy_producer(
            kind="pr_local_ci", feedback_id="local-ci-audit-v2:" + "b" * 40,
            suffix=":supervised-v4", evidence={
                "repository": "acme/widgets", "pr_number": 17,
                "expected_head_sha": "a" * 40}),
    }
    key, body = variants[problem]()
    add_legacy_rows(path, [wanted, (key, body)])
    assert identity_lookup() != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "legacy data qualification required" in captured.err
    assert "UNRELATED-PRIVATE" not in captured.err
    assert "other/repository" not in captured.err


@pytest.mark.parametrize("raw", ["{}", feedback_identity(pr_number=True), feedback_identity(feedback_kind="unknown"),
                                  feedback_identity(extra="x"), '{"repository":"a/b","repository":"a/b"}',
                                  feedback_identity() + " "])
def test_identity_selector_input_is_strict_before_open(board, monkeypatch, capsys, raw):
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("invalid identity opened DB"))
    assert identity_lookup(raw) != 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("bound", ["rows", "bytes", "time"])
def test_identity_index_bound_holds_whole_result(board, monkeypatch, capsys, legacy_producer, bound):
    from hermes_cli import kanban_intake_lookup as lookup
    _, path = board
    add_legacy_rows(path, [legacy_producer(), legacy_producer(feedback_id="100")])
    if bound == "rows":
        monkeypatch.setattr(lookup, "_MAX_LEGACY_ROWS", 1)
    elif bound == "bytes":
        monkeypatch.setattr(lookup, "_MAX_LEGACY_SCAN_BYTES", 1)
    else:
        ticks = iter([10.0, 12.0])
        monkeypatch.setattr(lookup, "_monotonic", lambda: next(ticks, 12.0))
    assert identity_lookup() != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "legacy data qualification required" in captured.err


def test_identity_lookup_supervised_identity_requires_exact_reconstructible_hash(board, capsys, legacy_producer):
    _, path = board
    data = {"repository": "acme/widgets", "pr_number": 17, "expected_head_sha": "a" * 40}
    add_legacy_rows(path, [legacy_producer(kind="pr_local_ci", feedback_id="local-ci-audit-v2",
                                         suffix=":supervised-v4", evidence=data),
                           legacy_producer(kind="pr_local_ci", feedback_id="local-ci-audit-v2:" + "b" * 40,
                                           head="b" * 40, suffix=":supervised-v4:dispatch-2", evidence={
                                               **data, "expected_head_sha": "b" * 40,
                                               "feedback_kind": "pr_local_ci",
                                               "feedback_id": "local-ci-audit-v2:" + "b" * 40})])
    assert identity_lookup() == 0
    assert json.loads(capsys.readouterr().out) == []


def test_intent_review_with_colon_id_and_reconciled_body_still_holds(board, capsys, legacy_producer):
    _, path = board
    add_legacy_rows(path, [legacy_producer(feedback_id="99:with:colons", intent=True, reconcile=True)])
    assert identity_lookup(feedback_identity(feedback_id="99:with:colons")) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_deep_malformed_legacy_json_is_a_named_qualification_hold(board, capsys):
    _, path = board
    add_legacy_rows(path, [(LEGACY_KEY, '{"x":' + '[' * 12000 + '0' + ']' * 12000 + '}')])
    assert identity_lookup() != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "legacy data qualification required" in captured.err


def test_identity_deadline_is_checked_after_classification(board, monkeypatch, capsys, legacy_producer):
    from hermes_cli import kanban_intake_lookup as lookup
    _, path = board
    add_legacy_rows(path, [legacy_producer()])
    classified = [False]
    real_classify = lookup._qualified_legacy_identity

    def classify(task):
        value = real_classify(task)
        classified[0] = True
        return value

    monkeypatch.setattr(lookup, "_qualified_legacy_identity", classify)
    monkeypatch.setattr(lookup, "_monotonic", lambda: 12.0 if classified[0] else 10.0)
    assert identity_lookup() != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "legacy data qualification required" in captured.err
