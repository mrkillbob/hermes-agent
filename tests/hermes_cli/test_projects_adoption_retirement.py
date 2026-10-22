"""The one exact same-root legacy registration can be retired without rewriting history."""

import json
import sqlite3
import stat
import subprocess
import threading

import pytest

from hermes_cli import kanban_db as kb, kanban_transfer, projects_db
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.sqlite_util import write_txn


@pytest.fixture
def duplicate(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "root"
    profile = root / "profiles" / "task-orchestrator"
    profile.mkdir(parents=True)
    (profile / "profile.yaml").write_text("name: task-orchestrator\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(root))
    repo = tmp_path.resolve() / "Hermes-agent"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    with projects_db.connect_closing(root / "projects.db") as conn:
        canonical_id = projects_db.create_project(
            conn,
            name="Hermes Agent",
            slug="hermes-agent",
            primary_path=str(repo),
            board_slug="default",
        )
    with projects_db.connect_closing(profile / "projects.db") as conn:
        legacy_id = projects_db.create_project(
            conn, name="hermes-agent", slug="hermes-agent"
        )
        with projects_db.write_txn(conn):
            conn.execute(
                "UPDATE projects SET id=?, primary_path=? WHERE id=?",
                ("p_7848dc64", str(repo), legacy_id),
            )
        projects_db.add_folder(conn, "p_7848dc64", str(repo), is_primary=True)
        legacy_id = "p_7848dc64"
        legacy = dict(
            conn.execute("SELECT * FROM projects WHERE id=?", (legacy_id,)).fetchone()
        )
        folder = dict(
            conn.execute(
                "SELECT * FROM project_folders WHERE project_id=?", (legacy_id,)
            ).fetchone()
        )
    kb.create_board("default", project_id=canonical_id, default_workdir=str(repo))
    kb.create_board("tradingbot-burndown")

    # The card is created as the named profile, so project resolution must use
    # that profile's registry rather than the canonical root registry.
    monkeypatch.setenv("HERMES_HOME", str(profile))
    task_id = "t_1d9b678d"
    worktree = "/Users/mikedemott/hermes-agent/.worktrees/t_1d9b678d"
    branch = "hermes-agent/t_1d9b678d-audit-review-upstream-pr-118213-probe-bo"
    with connect_closing(board="default") as conn:
        created = kb.create_task(
            conn,
            title="Retained completed audit card",
            project_id=legacy_id,
            board="default",
            workspace_kind="worktree",
            workspace_path=worktree,
            branch_name=branch,
        )
        if created != task_id:
            with kb.write_txn(conn):
                conn.execute("UPDATE tasks SET id=? WHERE id=?", (task_id, created))
                conn.execute(
                    "UPDATE task_events SET task_id=? WHERE task_id=?",
                    (task_id, created),
                )
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))
            conn.execute(
                "INSERT INTO task_runs (task_id,status,started_at,ended_at,outcome) VALUES (?,?,?,?,?)",
                (task_id, "done", 1, 2, "completed"),
            )
            for index in range(7):
                conn.execute(
                    "INSERT INTO task_events (task_id,kind,payload,created_at) VALUES (?,?,?,?)",
                    (
                        task_id,
                        "retained-fixture-event",
                        json.dumps({"ordinal": index}),
                        index + 2,
                    ),
                )

    expected_refs = {
        "default": [
            {
                "id": task_id,
                "status": "done",
                "project_id": legacy_id,
                "workspace_kind": "worktree",
                "workspace_path": worktree,
                "branch_name": branch,
                "current_run_id": None,
                "claim_lock": None,
                "claim_expires": None,
                "worker_pid": None,
                "run_count": 1,
                "event_count": 8,
            }
        ],
        "tradingbot-burndown": [],
    }
    return {
        "source_registry": root / "projects.db",
        "target_registry": profile / "projects.db",
        "target_profile": "task-orchestrator",
        "expected_project_id": canonical_id,
        "expected_root": repo,
        "expected_board": "default",
        "legacy_project_id": legacy_id,
        "expected_legacy_project": legacy,
        "expected_legacy_folders": [folder],
        "expected_active_id": None,
        "expected_boards": ("default", "tradingbot-burndown"),
        "expected_references": expected_refs,
        "backup_path": tmp_path.resolve() / "receipt.json",
        "task_id": task_id,
    }


def reconcile(args):
    from hermes_cli.projects_reconcile import retire_exact_duplicate_and_adopt

    return retire_exact_duplicate_and_adopt(**args)


def test_root_operator_cli_reconciles_exact_reviewed_duplicate(
    duplicate, monkeypatch, capsys
):
    import argparse

    from hermes_cli.projects_cmd import build_parser, projects_command

    monkeypatch.setenv("HERMES_HOME", str(duplicate["source_registry"].parent))
    expectation_path = duplicate["backup_path"].with_name("expectations.json")
    expectations = {
        key: duplicate[key]
        for key in (
            "expected_project_id",
            "expected_root",
            "expected_board",
            "expected_legacy_project",
            "expected_legacy_folders",
            "expected_active_id",
            "expected_boards",
            "expected_references",
        )
    }
    expectations["expected_root"] = str(expectations["expected_root"])
    expectations["expected_boards"] = list(expectations["expected_boards"])
    expectation_path.write_text(json.dumps(expectations))
    parser = argparse.ArgumentParser()
    build_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "project",
            "canonicalize-duplicate",
            duplicate["legacy_project_id"],
            "--target-profile",
            duplicate["target_profile"],
            "--expectations",
            str(expectation_path),
            "--receipt",
            str(duplicate["backup_path"]),
        ]
    )

    assert projects_command(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "canonicalized"
    assert result["retired_project_id"] == duplicate["legacy_project_id"]
    assert duplicate["backup_path"].is_file()
    with projects_db.connect_closing(duplicate["target_registry"]) as conn:
        assert (
            projects_db.get_project(conn, duplicate["expected_project_id"]) is not None
        )


def test_root_operator_cli_refuses_named_profile_context(
    duplicate, monkeypatch, capsys
):
    import argparse

    from hermes_cli.projects_cmd import build_parser, projects_command

    monkeypatch.setenv("HERMES_HOME", str(duplicate["target_registry"].parent))
    parser = argparse.ArgumentParser()
    build_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "project",
            "canonicalize-duplicate",
            duplicate["legacy_project_id"],
            "--target-profile",
            duplicate["target_profile"],
            "--expectations",
            str(duplicate["backup_path"].with_name("missing-expectations.json")),
            "--receipt",
            str(duplicate["backup_path"]),
        ]
    )

    assert projects_command(args) == 2
    assert "root-profile operator" in capsys.readouterr().err
    assert not duplicate["backup_path"].exists()


def test_exact_duplicate_is_retired_without_rewriting_history_or_board(duplicate):
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    with projects_db.connect_closing(args["source_registry"]) as conn, write_txn(conn):
        conn.execute(
            "UPDATE projects SET description=?, icon=?, color=? WHERE id=?",
            (
                "preserve source metadata",
                "folder",
                "#123abc",
                args["expected_project_id"],
            ),
        )
    source_before = args["source_registry"].read_bytes()
    board_before = kb.board_metadata_path("default").read_bytes()
    with connect_closing(board="default") as conn:
        history_before = kanban_transfer._selected_task_rows(
            conn, [duplicate["task_id"]]
        )

    result = reconcile(args)

    assert result["status"] == "canonicalized"
    assert result["project_id"] == args["expected_project_id"]
    assert result["retired_project_id"] == args["legacy_project_id"]
    assert args["backup_path"].is_file()
    assert stat.S_IMODE(args["backup_path"].stat().st_mode) == 0o600
    receipt = json.loads(args["backup_path"].read_text())
    assert receipt["target_before"]["projects"] == [args["expected_legacy_project"]]
    assert (
        receipt["target_before"]["project_folders"] == args["expected_legacy_folders"]
    )
    assert receipt["references"]["default"]["tasks"][0]["id"] == duplicate["task_id"]

    with projects_db.connect_closing(args["target_registry"]) as conn:
        canonical = projects_db.get_project(conn, args["expected_project_id"])
        retired = projects_db.get_project(conn, args["legacy_project_id"])
        assert (
            canonical
            and canonical.board_slug == "default"
            and canonical.primary_path == str(args["expected_root"])
            and canonical.description == "preserve source metadata"
            and canonical.icon == "folder"
            and canonical.color == "#123abc"
        )
        assert retired and retired.archived and retired.primary_path is None
        assert retired.slug == "hermes-agent-retired-p_7848dc64"
        assert retired.folders == []
        assert projects_db.get_active_id(conn) is None
    assert args["source_registry"].read_bytes() == source_before
    assert kb.board_metadata_path("default").read_bytes() == board_before
    with connect_closing(board="default") as conn:
        assert (
            kanban_transfer._selected_task_rows(conn, [duplicate["task_id"]])
            == history_before
        )


@pytest.mark.parametrize("active", [None, "p_7848dc64", "unrelated-active"])
def test_active_selection_is_preserved_or_repointed_only_for_retired_identity(
    duplicate, active
):
    with projects_db.connect_closing(duplicate["target_registry"]) as conn:
        if active is not None:
            projects_db.set_active(conn, active)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    args["expected_active_id"] = active
    reconcile(args)
    with projects_db.connect_closing(args["target_registry"]) as conn:
        expected = (
            args["expected_project_id"]
            if active == args["legacy_project_id"]
            else active
        )
        assert projects_db.get_active_id(conn) == expected


def _seed_nonterminal(duplicate):
    with connect_closing(board="default") as conn, kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET status='ready' WHERE id=?", (duplicate["task_id"],)
        )


def _seed_residual_claim_expiry(duplicate):
    with connect_closing(board="default") as conn, kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET claim_expires=123 WHERE id=?",
            (duplicate["task_id"],),
        )


def _seed_claimed_run(duplicate):
    _seed_run_status(duplicate, "claimed")


def _seed_unknown_run(duplicate):
    _seed_run_status(duplicate, "unknown")


def _seed_run_status(duplicate, status):
    with connect_closing(board="default") as conn, kb.write_txn(conn):
        conn.execute(
            "UPDATE task_runs SET status=? WHERE task_id=?",
            (status, duplicate["task_id"]),
        )


def _seed_extra_reference(duplicate):
    with connect_closing(board="default") as conn:
        kb.create_task(
            conn,
            title="Unexpected extra reference",
            project_id=duplicate["legacy_project_id"],
            board="default",
        )


def _seed_registry_drift(duplicate):
    with projects_db.connect_closing(duplicate["target_registry"]) as conn:
        projects_db.update_project(conn, duplicate["legacy_project_id"], name="changed")


def _seed_archived_reference(duplicate):
    kb.create_board("archived")
    with connect_closing(board="archived") as conn:
        kb.create_task(
            conn,
            title="Hidden archived task",
            project_id=duplicate["legacy_project_id"],
            board="archived",
            initial_status="todo",
        )
    kb.remove_board("archived", archive=True)


def _seed_unexpected_board(duplicate):
    kb.create_board("unexpected")


_DRIFT_SEEDERS = {
    "nonterminal": _seed_nonterminal,
    "residual_claim_expiry": _seed_residual_claim_expiry,
    "claimed_run": _seed_claimed_run,
    "unknown_run": _seed_unknown_run,
    "extra_reference": _seed_extra_reference,
    "registry": _seed_registry_drift,
    "board_inventory": _seed_unexpected_board,
    "archived_board": _seed_archived_reference,
}


@pytest.mark.parametrize(
    "drift",
    [
        "nonterminal",
        "residual_claim_expiry",
        "claimed_run",
        "unknown_run",
        "extra_reference",
        "registry",
        "board_inventory",
        "archived_board",
    ],
)
def test_unexpected_or_nonterminal_state_refuses_before_receipt(duplicate, drift):
    _DRIFT_SEEDERS[drift](duplicate)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    with pytest.raises(ValueError):
        reconcile(args)
    assert not args["backup_path"].exists()


def test_backup_collision_refuses_without_registry_change(duplicate):
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    args["backup_path"].write_text("occupied")
    before = args["target_registry"].read_bytes()
    with pytest.raises(ValueError, match="recovery"):
        reconcile(args)
    assert args["target_registry"].read_bytes() == before
    assert args["backup_path"].read_text() == "occupied"


def test_registry_drift_after_receipt_refuses_commit(duplicate, monkeypatch):
    from hermes_cli import projects_reconcile

    original = projects_reconcile._publish_private_receipt

    def publish_then_drift(path, document):
        original(path, document)
        with sqlite3.connect(duplicate["target_registry"]) as conn:
            conn.execute(
                "UPDATE projects SET name='raced' WHERE id=?",
                (duplicate["legacy_project_id"],),
            )

    monkeypatch.setattr(
        projects_reconcile, "_publish_private_receipt", publish_then_drift
    )
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    with pytest.raises(ValueError, match="changed after recovery receipt"):
        reconcile(args)
    assert args["backup_path"].is_file()
    with projects_db.connect_closing(args["target_registry"]) as conn:
        assert projects_db.get_project(conn, args["expected_project_id"]) is None


def test_board_metadata_locks_precede_registry_and_board_writers(
    duplicate, monkeypatch
):
    from contextlib import contextmanager

    from hermes_cli import projects_reconcile

    events = []
    original_registry_write = projects_reconcile._registry_write
    original_board_connection = projects_reconcile._board_connection
    original_board_lock = projects_reconcile.board_ownership_lock
    receipt_path = duplicate["backup_path"]

    def registry_write(path):
        events.append("registry")
        return original_registry_write(path)

    def board_connection(slug, *, readonly):
        if not readonly and receipt_path.exists():
            events.append("board-db")
        return original_board_connection(slug, readonly=readonly)

    @contextmanager
    def board_lock(slug):
        if receipt_path.exists():
            events.append("board-lock")
        with original_board_lock(slug):
            yield

    monkeypatch.setattr(projects_reconcile, "_registry_write", registry_write)
    monkeypatch.setattr(projects_reconcile, "_board_connection", board_connection)
    monkeypatch.setattr(projects_reconcile, "board_ownership_lock", board_lock)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}

    reconcile(args)

    first_board_lock = events.index("board-lock")
    first_registry = events.index("registry")
    first_board_writer = events.index("board-db")
    assert first_board_lock < first_registry < first_board_writer


def test_uncertain_target_commit_reports_receipt_and_persisted_state(
    duplicate, monkeypatch
):
    import sqlite3

    from hermes_cli import projects_reconcile

    original = projects_reconcile._registry_write

    class CommitThenRaise(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            result = super().execute(sql, *args, **kwargs)
            if sql.strip().upper() == "COMMIT":
                raise OSError("simulated response loss after durable COMMIT")
            return result

    def registry_write(path):
        if path != duplicate["target_registry"]:
            return original(path)
        conn = sqlite3.connect(
            path.as_uri() + "?mode=rw", uri=True, timeout=5, factory=CommitThenRaise
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(projects_reconcile, "_registry_write", registry_write)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    with pytest.raises(RuntimeError, match="commit outcome is uncertain"):
        reconcile(args)
    assert args["backup_path"].is_file()
    with projects_db.connect_closing(args["target_registry"]) as conn:
        assert projects_db.get_project(conn, args["expected_project_id"]) is not None


def test_target_commit_and_rollback_failure_preserves_uncertainty(
    duplicate, monkeypatch
):
    import sqlite3

    from hermes_cli import projects_reconcile

    original = projects_reconcile._registry_write

    class CommitAndRollbackFail(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.strip().upper() in {"COMMIT", "ROLLBACK"}:
                raise OSError("simulated SQLite transaction I/O failure")
            return super().execute(sql, *args, **kwargs)

    def registry_write(path):
        if path != duplicate["target_registry"]:
            return original(path)
        conn = sqlite3.connect(
            path.as_uri() + "?mode=rw",
            uri=True,
            timeout=5,
            factory=CommitAndRollbackFail,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(projects_reconcile, "_registry_write", registry_write)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    with pytest.raises(RuntimeError, match="outcomes are uncertain"):
        reconcile(args)
    assert args["backup_path"].is_file()


@pytest.mark.parametrize("bad_value", [None, [7], [{}]])
def test_malformed_nested_reference_expectation_is_clean_refusal(duplicate, bad_value):
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    args["expected_references"] = {**args["expected_references"], "default": bad_value}

    with pytest.raises(ValueError, match="reference expectation"):
        reconcile(args)
    assert not args["backup_path"].exists()


def test_new_board_owner_is_serialized_after_canonicalization_commit(
    duplicate, monkeypatch, capsys
):
    import argparse

    from hermes_cli import projects_reconcile
    from hermes_cli.projects_cmd import build_parser, projects_command

    monkeypatch.setenv("HERMES_HOME", str(duplicate["source_registry"].parent))
    expectation_path = duplicate["backup_path"].with_name("expectations.json")
    expectations = {
        key: duplicate[key]
        for key in (
            "expected_project_id",
            "expected_root",
            "expected_board",
            "expected_legacy_project",
            "expected_legacy_folders",
            "expected_active_id",
            "expected_boards",
            "expected_references",
        )
    }
    expectations["expected_root"] = str(expectations["expected_root"])
    expectations["expected_boards"] = list(expectations["expected_boards"])
    expectation_path.write_text(json.dumps(expectations))
    parser = argparse.ArgumentParser()
    build_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "project",
            "canonicalize-duplicate",
            duplicate["legacy_project_id"],
            "--target-profile",
            duplicate["target_profile"],
            "--expectations",
            str(expectation_path),
            "--receipt",
            str(duplicate["backup_path"]),
        ]
    )

    started = threading.Event()
    created = threading.Event()
    committed = threading.Event()
    created_before_commit = []
    errors = []
    original_commit = projects_reconcile._commit_target_registry
    thread_holder = []

    def publish_new_owner():
        from hermes_cli import kanban_db as board_db

        started.set()
        try:
            board_db.create_board(
                "late-owner", project_id=duplicate["legacy_project_id"]
            )
            created_before_commit.append(not committed.is_set())
        except BaseException as exc:  # collected for the parent test thread
            errors.append(exc)
        finally:
            created.set()

    def commit_after_starting_publisher(conn):
        thread = threading.Thread(target=publish_new_owner)
        thread_holder.append(thread)
        thread.start()
        assert started.wait(2)
        # The new slug must wait for the catalog reservation while the target
        # registry is at its commit boundary.
        assert not created.wait(0.1)
        original_commit(conn)
        committed.set()

    monkeypatch.setattr(
        projects_reconcile, "_commit_target_registry", commit_after_starting_publisher
    )
    assert projects_command(args) == 0
    assert capsys.readouterr().out
    assert created.wait(3)
    thread_holder[0].join(3)
    assert not thread_holder[0].is_alive()
    assert not errors
    assert created_before_commit == [False]


@pytest.mark.platforms("windows")
def test_windows_receipt_publication_allows_registry_reconciliation(duplicate):
    """Windows cannot open directories with os.open; file fsync still precedes publication."""
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    result = reconcile(args)
    assert result["status"] == "canonicalized"
    receipt = json.loads(args["backup_path"].read_text())
    assert receipt["target_before"]["projects"] == [args["expected_legacy_project"]]
    with projects_db.connect_closing(args["target_registry"]) as conn:
        assert projects_db.get_project(conn, args["expected_project_id"]) is not None
        assert projects_db.get_project(conn, args["legacy_project_id"]).archived


@pytest.mark.parametrize("failure", ["file-sync", "link"])
def test_receipt_failure_before_publication_leaves_registries_unchanged(
    duplicate, monkeypatch, failure
):
    from hermes_cli import projects_reconcile

    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    before = {key: args[key].read_bytes() for key in ("source_registry", "target_registry")}

    def fail(*_args, **_kwargs):
        raise OSError("injected receipt publication failure")

    monkeypatch.setattr(projects_reconcile.os, "fsync" if failure == "file-sync" else "link", fail)
    with pytest.raises(OSError, match="receipt publication failure"):
        reconcile(args)
    assert not args["backup_path"].exists()
    assert all(args[key].read_bytes() == value for key, value in before.items())


@pytest.mark.platforms("posix")
def test_directory_sync_failure_preserves_receipt_and_refuses_registry_mutation(
    duplicate, monkeypatch
):
    from hermes_cli import projects_reconcile

    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    before = {key: args[key].read_bytes() for key in ("source_registry", "target_registry")}
    original_open = projects_reconcile.os.open

    def directory_open_fails(path, flags, *rest, **kwargs):
        if path == args["backup_path"].parent:
            raise PermissionError("injected directory-open refusal")
        return original_open(path, flags, *rest, **kwargs)

    monkeypatch.setattr(projects_reconcile.os, "open", directory_open_fails)
    with pytest.raises(PermissionError, match="directory-open refusal"):
        reconcile(args)
    receipt_bytes = args["backup_path"].read_bytes()
    receipt = json.loads(receipt_bytes)
    assert receipt["target_before"]["projects"] == [args["expected_legacy_project"]]
    assert all(args[key].read_bytes() == value for key, value in before.items())
    with pytest.raises(ValueError, match="recovery receipt"):
        reconcile(args)
    assert args["backup_path"].read_bytes() == receipt_bytes


@pytest.mark.parametrize("status", ["archived", "failed", "cancelled"])
def test_reviewed_terminal_cards_preserve_all_history(duplicate, status):
    with connect_closing(board="default") as conn, kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, duplicate["task_id"]))
        before = kanban_transfer._selected_task_rows(conn, [duplicate["task_id"]])
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    args["expected_references"]["default"][0]["status"] = status
    assert reconcile(args)["status"] == "canonicalized"
    with connect_closing(board="default") as conn:
        assert kanban_transfer._selected_task_rows(conn, [duplicate["task_id"]]) == before


@pytest.mark.parametrize("status", ["ready", "in_progress", "unknown"])
def test_reviewed_active_or_unknown_cards_still_refuse(duplicate, status):
    with connect_closing(board="default") as conn, kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, duplicate["task_id"]))
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    args["expected_references"]["default"][0]["status"] = status
    before = args["target_registry"].read_bytes()
    with pytest.raises(ValueError, match="terminal unclaimed"):
        reconcile(args)
    assert args["target_registry"].read_bytes() == before
    assert not args["backup_path"].exists()


def _leave_default_uninitialized(duplicate):
    from contextlib import closing
    from hermes_cli.kanban_db_boards import write_board_metadata

    named = "tradingbot-burndown"
    with closing(sqlite3.connect(kb.kanban_db_path("default"))) as source:
        with closing(sqlite3.connect(kb.kanban_db_path(named))) as target:
            source.backup(target)
    kb.kanban_db_path("default").unlink()
    kb.board_metadata_path("default").unlink()
    write_board_metadata(named, project_id=duplicate["expected_project_id"])
    with projects_db.connect_closing(duplicate["source_registry"]) as conn, write_txn(conn):
        conn.execute("UPDATE projects SET board_slug=? WHERE id=?", (named, duplicate["expected_project_id"]))
    duplicate["expected_board"] = named
    duplicate["expected_references"][named] = duplicate["expected_references"]["default"]
    duplicate["expected_references"]["default"] = []


def test_synthesized_default_board_remains_absent_after_reconciliation(duplicate):
    _leave_default_uninitialized(duplicate)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    assert "default" in {row["slug"] for row in kb.list_boards()}
    assert reconcile(args)["status"] == "canonicalized"
    assert not kb.kanban_db_path("default").exists()
    assert not kb.board_metadata_path("default").exists()
    receipt = json.loads(args["backup_path"].read_text())
    assert receipt["references"]["default"]["summaries"] == []


def test_absent_board_cannot_satisfy_nonempty_reviewed_references(duplicate):
    _leave_default_uninitialized(duplicate)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    args["expected_references"]["default"] = list(args["expected_references"]["tradingbot-burndown"])
    before = args["target_registry"].read_bytes()
    with pytest.raises(ValueError):
        reconcile(args)
    assert args["target_registry"].read_bytes() == before
    assert not args["backup_path"].exists()
    assert not kb.kanban_db_path("default").exists()


def test_metadata_only_board_with_empty_expectation_stays_uninitialized(duplicate):
    path = kb.kanban_db_path("tradingbot-burndown")
    path.unlink()
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    assert reconcile(args)["status"] == "canonicalized"
    assert not path.exists()
    assert kb.board_metadata_path("tradingbot-burndown").exists()


@pytest.mark.parametrize("drift", ["database-appears", "orphaned-wal", "stat-error"])
def test_absent_board_identity_or_recovery_drift_refuses_commit(duplicate, monkeypatch, drift):
    from pathlib import Path
    from hermes_cli import projects_reconcile

    _leave_default_uninitialized(duplicate)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    before = args["target_registry"].read_bytes()
    original = projects_reconcile._publish_private_receipt
    missing = kb.kanban_db_path("default")
    original_lstat = Path.lstat

    def publish_then_drift(path, document):
        result = original(path, document)
        if drift == "database-appears":
            with connect_closing(board="default"):
                pass
        elif drift == "orphaned-wal":
            Path(str(missing) + "-wal").write_bytes(b"unreviewed recovery state")
        else:
            def refuse_stat(candidate, *a, **kw):
                if candidate == missing:
                    raise PermissionError("injected missing-board stat failure")
                return original_lstat(candidate, *a, **kw)
            monkeypatch.setattr(Path, "lstat", refuse_stat)
        return result

    monkeypatch.setattr(projects_reconcile, "_publish_private_receipt", publish_then_drift)
    with pytest.raises((ValueError, PermissionError)):
        reconcile(args)
    assert args["target_registry"].read_bytes() == before
    assert args["backup_path"].exists()


def test_absent_board_creation_is_fenced_through_target_commit(duplicate, monkeypatch):
    from hermes_cli import kanban_db_connect as kbc, projects_reconcile

    _leave_default_uninitialized(duplicate)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    observed = threading.Event()
    finished = threading.Event()
    committed = threading.Event()
    outcomes = []
    producer = None
    original_try = kbc._try_lock_nb
    original_commit = projects_reconcile._commit_target_registry

    def try_lock(handle):
        acquired = original_try(handle)
        if threading.current_thread() is producer and not acquired:
            observed.set()
        return acquired

    def produce():
        try:
            with connect_closing(board="default") as conn:
                task_id = kb.create_task(conn, title="Concurrent legacy card", board="default",
                    project_id=args["legacy_project_id"], workspace_kind="scratch")
                outcomes.append((task_id, committed.is_set()))
        except Exception as exc:
            outcomes.append(exc)
        finally:
            finished.set()
            observed.set()

    def commit(conn):
        nonlocal producer
        producer = threading.Thread(target=produce, name="ordinary-board-creator")
        producer.start()
        assert observed.wait(5), "producer neither contended nor completed"
        assert not finished.is_set(), f"ordinary producer escaped before commit: {outcomes!r}"
        assert not kb.kanban_db_path("default").exists(), "cached connect recreated the absent DB"
        original_commit(conn)
        committed.set()

    monkeypatch.setattr(kbc, "_try_lock_nb", try_lock)
    monkeypatch.setattr(projects_reconcile, "_commit_target_registry", commit)
    try:
        assert reconcile(args)["status"] == "canonicalized"
    finally:
        if producer is not None:
            producer.join(8)
    assert finished.is_set()
    assert len(outcomes) == 1 and isinstance(outcomes[0], tuple)
    task_id, created_after_commit = outcomes[0]
    assert created_after_commit
    # Explicit historical IDs remain permitted by the existing task API after
    # reconciliation. The reservation orders that later write after commit.
    with connect_closing(board="default") as conn:
        assert [row["id"] for row in conn.execute(
            "SELECT id FROM tasks WHERE project_id=?", (args["legacy_project_id"],)
        )] == [task_id]


@pytest.mark.parametrize("drift", [None, "unfinished", "unknown"])
def test_public_completion_run_requires_known_ended_history(duplicate, drift):
    with connect_closing(board="default") as conn:
        with kb.write_txn(conn):
            conn.execute("DELETE FROM task_runs WHERE task_id=?", (duplicate["task_id"],))
            conn.execute(
                "UPDATE tasks SET status='ready',workspace_kind='dir',workspace_path=?,branch_name=NULL WHERE id=?",
                (str(duplicate["expected_root"]), duplicate["task_id"]),
            )
        duplicate["expected_references"]["default"][0].update(
            workspace_kind="dir", workspace_path=str(duplicate["expected_root"]), branch_name=None,
        )
        assert kb.complete_task(
            conn, duplicate["task_id"], result="Synthetic reviewed result",
            summary="Synthetic handoff", metadata={"fixture": True},
            fire_lifecycle_hook=False, board="default",
        )
        runs = conn.execute("SELECT * FROM task_runs WHERE task_id=?", (duplicate["task_id"],)).fetchall()
        assert len(runs) == 1 and runs[0]["status"] == "completed" and runs[0]["ended_at"] is not None
        if drift:
            with kb.write_txn(conn):
                if drift == "unfinished":
                    conn.execute("UPDATE task_runs SET ended_at=NULL WHERE task_id=?", (duplicate["task_id"],))
                else:
                    conn.execute("UPDATE task_runs SET status='unknown' WHERE task_id=?", (duplicate["task_id"],))
        before = kanban_transfer._selected_task_rows(conn, [duplicate["task_id"]])
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    args["expected_references"]["default"][0].update(
        run_count=len(before["task_runs"]), event_count=len(before["task_events"])
    )
    registry_before = args["target_registry"].read_bytes()
    if drift is None:
        assert reconcile(args)["status"] == "canonicalized"
    else:
        with pytest.raises(ValueError, match="terminal unclaimed"):
            reconcile(args)
        assert args["target_registry"].read_bytes() == registry_before
        assert not args["backup_path"].exists()
    with connect_closing(board="default") as conn:
        assert kanban_transfer._selected_task_rows(conn, [duplicate["task_id"]]) == before


def test_canonical_source_writer_is_reserved_through_target_commit(duplicate, monkeypatch):
    from hermes_cli import projects_reconcile

    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    outcomes = []
    original = projects_reconcile._commit_target_registry

    def commit(conn):
        def archive_source():
            try:
                with sqlite3.connect(args["source_registry"], isolation_level=None, timeout=0) as writer:
                    outcomes.append(projects_db.archive_project(writer, args["expected_project_id"]))
            except sqlite3.OperationalError as exc:
                outcomes.append(exc)
        writer = threading.Thread(target=archive_source)
        writer.start()
        writer.join(5)
        assert not writer.is_alive()
        original(conn)
    monkeypatch.setattr(projects_reconcile, "_commit_target_registry", commit)
    assert reconcile(args)["status"] == "canonicalized"
    assert len(outcomes) == 1 and isinstance(outcomes[0], sqlite3.OperationalError)
    assert "locked" in str(outcomes[0]).lower()
    with projects_db.connect_closing(args["source_registry"]) as conn:
        assert not projects_db.get_project(conn, args["expected_project_id"]).archived
        assert projects_db.archive_project(conn, args["expected_project_id"])


@pytest.mark.parametrize("after_commit", [False, True])
def test_source_reservation_cleanup_failure_reports_committed_target(duplicate, monkeypatch, after_commit):
    from hermes_cli import projects_reconcile

    original = projects_reconcile._registry_write
    class FailingSourceCommit(sqlite3.Connection):
        def execute(self, sql, *a, **kw):
            if sql.strip().upper() == "COMMIT":
                if after_commit:
                    super().execute(sql, *a, **kw)
                raise OSError("synthetic source reservation release failure")
            return super().execute(sql, *a, **kw)
    def source_writer(path):
        if path != duplicate["source_registry"]:
            return original(path)
        conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, factory=FailingSourceCommit)
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(projects_reconcile, "_registry_write", source_writer)
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    with pytest.raises(RuntimeError, match="canonicalization committed but .*fence release failed"):
        reconcile(args)
    assert args["backup_path"].is_file()
    with projects_db.connect_closing(args["target_registry"]) as conn:
        assert projects_db.get_project(conn, args["expected_project_id"]) is not None
        assert projects_db.get_project(conn, args["legacy_project_id"]).archived


@pytest.mark.parametrize("active_run", [False, True], ids=["synthesized", "claimed"])
@pytest.mark.parametrize("drift", [None, "unfinished", "current", "unknown"])
def test_public_repair_route_history_requires_ended_unclaimed_runs(duplicate, monkeypatch, active_run, drift):
    monkeypatch.setattr(kb, "_fire_task_hook", lambda *a, **kw: None)
    with connect_closing(board="default") as conn:
        with kb.write_txn(conn):
            conn.execute("DELETE FROM task_runs WHERE task_id=?", (duplicate["task_id"],))
            conn.execute(
                "UPDATE tasks SET status='ready',workspace_kind='dir',workspace_path=?,branch_name=NULL WHERE id=?",
                (str(duplicate["expected_root"]), duplicate["task_id"]),
            )
        claimed = kb.claim_task(conn, duplicate["task_id"], claimer="synthetic-owner", board="default") if active_run else None
        if active_run:
            assert claimed is not None
        routed, _, _ = kb.route_worker_block_to_orchestrator(conn, duplicate["task_id"], reason="Synthetic unresolved handoff")
        assert routed
        run = conn.execute("SELECT * FROM task_runs WHERE task_id=?", (duplicate["task_id"],)).fetchone()
        assert run["status"] == ("routed" if active_run else "routed_to_repair_profile")
        assert run["ended_at"] is not None
        assert kb.complete_task(conn, duplicate["task_id"], result="Synthetic final result", fire_lifecycle_hook=False, board="default")
        if drift:
            with kb.write_txn(conn):
                if drift == "unfinished":
                    conn.execute("UPDATE task_runs SET ended_at=NULL WHERE id=?", (run["id"],))
                elif drift == "current":
                    conn.execute("UPDATE tasks SET current_run_id=? WHERE id=?", (run["id"], duplicate["task_id"]))
                else:
                    conn.execute("UPDATE task_runs SET status='unknown' WHERE id=?", (run["id"],))
        before = kanban_transfer._selected_task_rows(conn, [duplicate["task_id"]])
    args = {key: value for key, value in duplicate.items() if key != "task_id"}
    summary = args["expected_references"]["default"][0]
    summary.update({key: before["tasks"][0][key] for key in summary if key in before["tasks"][0]})
    summary.update(run_count=len(before["task_runs"]), event_count=len(before["task_events"]))
    if drift == "current":
        # The reviewed snapshot must be unclaimed; an actual current run drift
        # is refused rather than authorizing a live run through expectations.
        summary["current_run_id"] = None
    if drift is None:
        assert reconcile(args)["status"] == "canonicalized"
    else:
        with pytest.raises(ValueError, match="terminal unclaimed"):
            reconcile(args)
        assert not args["backup_path"].exists()
    with connect_closing(board="default") as conn:
        assert kanban_transfer._selected_task_rows(conn, [duplicate["task_id"]]) == before
