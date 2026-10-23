"""Explicit Triage correction preserves identity, history, and old workspaces."""

import json
import os
import subprocess
import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb, projects_db
from hermes_cli.kanban_db_connect import connect_closing


@pytest.fixture
def binding_case(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "home"))
    for key in ("HERMES_KANBAN_BOARD", "HERMES_KANBAN_DB"):
        monkeypatch.delenv(key, raising=False)
    roots = [tmp_path / name for name in ("old", "new")]
    for root in roots:
        root.mkdir()
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    with projects_db.connect_closing() as registry:
        old = projects_db.create_project(registry, name="Old", primary_path=str(roots[0]), board_slug="alpha")
        new = projects_db.create_project(registry, name="New", primary_path=str(roots[1]), board_slug="beta")
    kb.create_board("alpha", project_id=old)
    kb.create_board("beta", project_id=new)
    with connect_closing(board="alpha") as conn:
        task_id = kb.create_task(conn, title="Resolve repository binding", body="Preserve privacy hold",
                                 project_id=old, board="alpha")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='triage', consecutive_failures=2 WHERE id=?", (task_id,))
        kb.add_comment(conn, task_id, "reviewer", "Existing evidence")
        row = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
    return tmp_path, roots, old, new, task_id, row


def _correct(case, **changes):
    from hermes_cli.kanban_task_binding import BindingExpectation, correct_task_binding

    home, roots, _old, new, task_id, row = case
    args = dict(source_board="alpha", task_id=task_id, target_project_id=new,
                expected=BindingExpectation.from_task(row, project_root=str(roots[0])),
                target_root=str(roots[1]), reason="Verified source ownership correction",
                backup_path=str(home / "before.tar.gz"))
    args.update(changes)
    return correct_task_binding(**args)


def test_correction_preserves_id_hold_history_and_backup(binding_case):
    result = _correct(binding_case)
    _home, roots, _old, new, task_id, original = binding_case
    assert Path(result["backup"]).is_file()
    with sqlite3.connect(result["recovery_backup"]) as saved:
        saved.row_factory = sqlite3.Row
        assert dict(saved.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()) == original
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, task_id) is None
    with connect_closing(board="beta") as conn:
        row = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        assert row["project_id"] == new
        assert row["status"] == "triage"
        assert row["consecutive_failures"] == 2
        assert row["body"] == original["body"]
        assert row["workspace_path"] == str(roots[1] / ".worktrees" / task_id)
        assert conn.execute("SELECT body FROM task_comments WHERE task_id=?", (task_id,)).fetchone()[0] == "Existing evidence"
        event = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='project_binding_corrected'", (task_id,)).fetchone()
        assert json.loads(event[0])["old_binding"]["workspace_path"] == original["workspace_path"]


@pytest.mark.parametrize("column,value", [
    ("status", "ready"), ("claim_lock", "held"), ("current_run_id", 99), ("worker_pid", 123),
])
def test_correction_refuses_active_or_changed_task(binding_case, column, value):
    task_id = binding_case[4]
    with connect_closing(board="alpha") as conn, kb.write_txn(conn):
        conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, task_id))
    with pytest.raises(ValueError):
        _correct(binding_case)
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, task_id) is not None
    with connect_closing(board="beta") as conn:
        assert kb.get_task(conn, task_id) is None


def test_unknown_materialized_destination_is_preserved_and_refused(binding_case):
    target = binding_case[1][1] / ".worktrees" / binding_case[4]
    target.mkdir(parents=True)
    sentinel = target / "operator-work"
    sentinel.write_text("retain")
    with pytest.raises(ValueError, match="destination"):
        _correct(binding_case)
    assert sentinel.read_text() == "retain"


def test_old_materialized_tree_is_not_abandoned_without_owned_destination(binding_case):
    old_path = Path(binding_case[5]["workspace_path"])
    old_path.mkdir(parents=True)
    sentinel = old_path / "untracked-work"
    sentinel.write_text("retain")
    with pytest.raises(ValueError, match="destination"):
        _correct(binding_case)
    assert sentinel.read_text() == "retain"


def test_owned_destination_allows_correction_without_touching_old_tree(binding_case):
    _, roots, _, new, task_id, row = binding_case
    old_path = Path(row["workspace_path"])
    old_path.mkdir(parents=True)
    sentinel = old_path / "unknown-operator-work"
    sentinel.write_text("retain exactly")
    subprocess.run(["git", "-C", str(roots[1]), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "fixture"],
                   check=True, capture_output=True)
    with projects_db.connect_closing() as registry:
        project = projects_db.get_project(registry, new)
    branch = projects_db.branch_name_for(project, task_id, title=row["title"])
    target = roots[1] / ".worktrees" / task_id
    subprocess.run(["git", "-C", str(roots[1]), "worktree", "add", "-b", branch, str(target)],
                   check=True, capture_output=True)
    _correct(binding_case)
    assert sentinel.read_text() == "retain exactly"
    with connect_closing(board="beta") as conn:
        assert kb.get_task(conn, task_id).workspace_path == str(target)


def test_correction_refuses_dependency_crossing(binding_case):
    with connect_closing(board="alpha") as conn:
        child = kb.create_task(conn, title="Dependent work", board="alpha")
        kb.link_tasks(conn, binding_case[4], child)
    with pytest.raises(ValueError, match="dependenc"):
        _correct(binding_case)


def test_same_board_binding_correction_preserves_existing_history(binding_case):
    from hermes_cli.kanban_task_binding import BindingExpectation

    _home, roots, old, new, task_id, row = binding_case
    kb.write_board_metadata("alpha", project_id=new)
    with projects_db.connect_closing() as registry:
        projects_db.update_project(registry, new, board_slug="alpha")
    # The old project's own registration still points to alpha, but alpha now
    # belongs to the target. The exact source-board expectation is explicit.
    with projects_db.connect_closing() as registry:
        projects_db.update_project(registry, old, board_slug="old-board")
    kb.create_board("old-board", project_id=old)
    expected = BindingExpectation.from_task(row, project_root=str(roots[0]), board_project_id=new)
    _correct(binding_case, expected=expected)
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, task_id).project_id == new
        assert conn.execute("SELECT COUNT(*) FROM task_comments WHERE task_id=?", (task_id,)).fetchone()[0] == 1


def test_registry_is_reserved_through_transfer(binding_case, monkeypatch):
    from hermes_cli import kanban_transfer as transfer

    original = transfer.merge_board
    observed = []
    def guarded(*args, **kwargs):
        with sqlite3.connect(projects_db.projects_db_path(), timeout=0) as contender:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("UPDATE projects SET archived=1 WHERE id=?", (binding_case[3],))
        observed.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(transfer, "merge_board", guarded)
    _correct(binding_case)
    assert observed == [True]


def test_history_race_preserves_both_copies_and_exact_backup(binding_case, monkeypatch):
    from hermes_cli import kanban_transfer as transfer

    original = transfer._remove_source_task_rows
    def changed(board, task_ids, **kwargs):
        with connect_closing(board=board) as conn:
            kb.add_comment(conn, task_ids[0], "operator", "Evidence arrived after copy")
        return original(board, task_ids, **kwargs)
    monkeypatch.setattr(transfer, "_remove_source_task_rows", changed)
    with pytest.raises(ValueError, match="history changed"):
        _correct(binding_case)
    for board in ("alpha", "beta"):
        with connect_closing(board=board) as conn:
            assert kb.get_task(conn, binding_case[4]).status == "triage"
    assert (binding_case[0] / "before.tar.gz.source.sqlite3").is_file()


def test_destination_materialization_race_retains_source(binding_case, monkeypatch):
    from hermes_cli import kanban_transfer as transfer

    original = transfer._remove_source_task_rows
    target = binding_case[1][1] / ".worktrees" / binding_case[4]
    def changed(board, task_ids, **kwargs):
        target.mkdir(parents=True)
        (target / "unknown-work").write_text("retain")
        return original(board, task_ids, **kwargs)
    monkeypatch.setattr(transfer, "_remove_source_task_rows", changed)
    with pytest.raises(ValueError, match="destination"):
        _correct(binding_case)
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, binding_case[4]) is not None
    assert (target / "unknown-work").read_text() == "retain"


@pytest.mark.platforms("posix")
def test_recovery_snapshot_is_private_under_ordinary_umask(binding_case):
    previous = os.umask(0o022)
    try:
        result = _correct(binding_case)
    finally:
        os.umask(previous)
    assert Path(result["recovery_backup"]).stat().st_mode & 0o777 == 0o600


def test_existing_recovery_snapshot_is_never_clobbered(binding_case):
    recovery = binding_case[0] / "before.tar.gz.source.sqlite3"
    recovery.write_bytes(b"original recovery evidence")
    with pytest.raises(FileExistsError):
        _correct(binding_case)
    assert recovery.read_bytes() == b"original recovery evidence"
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, binding_case[4]) is not None


def _interrupt_after_copy(case, monkeypatch):
    from hermes_cli import kanban_transfer as transfer

    def interrupted(*args, **kwargs):
        raise ValueError("interrupted before prune")
    with monkeypatch.context() as patch:
        patch.setattr(transfer, "_remove_source_task_rows", interrupted)
        with pytest.raises(ValueError, match="interrupted before prune"):
            _correct(case)


def test_exact_retained_counterpart_can_resume_verified_transfer(binding_case, monkeypatch):
    _interrupt_after_copy(binding_case, monkeypatch)
    result = _correct(binding_case, backup_path=str(binding_case[0] / "retry.tar.gz"))
    assert result["already_copied"] is True
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, binding_case[4]) is None
    with connect_closing(board="beta") as conn:
        assert kb.get_task(conn, binding_case[4]).status == "triage"


@pytest.mark.parametrize("drift", ["history", "claim", "binding", "third_board_same_id"])
def test_retained_copy_exemption_rejects_drift_and_other_path_users(binding_case, monkeypatch, drift):
    _interrupt_after_copy(binding_case, monkeypatch)
    task_id = binding_case[4]
    with connect_closing(board="beta") as conn:
        target_path = kb.get_task(conn, task_id).workspace_path
        if drift == "history":
            kb.add_comment(conn, task_id, "operator", "new evidence")
        elif drift in {"claim", "binding"}:
            column, value = ("claim_lock", "live") if drift == "claim" else ("project_id", "p_unknown")
            with kb.write_txn(conn):
                conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, task_id))
    if drift == "third_board_same_id":
        kb.create_board("gamma")
        with connect_closing(board="gamma") as conn:
            other = kb.create_task(conn, title="Other board workspace owner", workspace_path=target_path, board="gamma")
            with kb.write_txn(conn):
                conn.execute("UPDATE tasks SET id=? WHERE id=?", (task_id, other))
    with pytest.raises(ValueError):
        _correct(binding_case, backup_path=str(binding_case[0] / "retry.tar.gz"))
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, task_id).status == "triage"


def _same_board_expectation(case):
    from hermes_cli.kanban_task_binding import BindingExpectation

    _, roots, old, new, _, row = case
    kb.write_board_metadata("alpha", project_id=new)
    with projects_db.connect_closing() as registry:
        projects_db.update_project(registry, new, board_slug="alpha")
        projects_db.update_project(registry, old, board_slug="old-board")
    kb.create_board("old-board", project_id=old)
    return BindingExpectation.from_task(row, project_root=str(roots[0]), board_project_id=new)


def test_same_board_export_race_refuses_new_workspace_user(binding_case, monkeypatch):
    from hermes_cli import kanban_transfer as transfer

    expected = _same_board_expectation(binding_case)
    original = transfer.export_board
    target = binding_case[1][1] / ".worktrees" / binding_case[4]
    rivals = []
    def acquired(*args, **kwargs):
        backup = original(*args, **kwargs)
        with connect_closing(board="alpha") as conn:
            rivals.append(kb.create_task(conn, title="Concurrent workspace owner", board="alpha",
                                          workspace_kind="dir", workspace_path=str(target)))
            assert kb.get_task(conn, rivals[-1]).workspace_path == str(target)
        return backup
    monkeypatch.setattr(transfer, "export_board", acquired)
    with pytest.raises(ValueError, match="destination workspace"):
        _correct(binding_case, expected=expected)
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, binding_case[4]).project_id == binding_case[2]
        assert kb.get_task(conn, rivals[0]).workspace_path == str(target)


def test_same_board_export_race_refuses_changed_workspace_policy(binding_case, monkeypatch):
    from hermes_cli import kanban_transfer as transfer

    expected = _same_board_expectation(binding_case)
    original = transfer.export_board
    root = binding_case[1][1]
    frozen_target = root / ".worktrees" / binding_case[4]
    def redirected(*args, **kwargs):
        backup = original(*args, **kwargs)
        (kb.kanban_home() / "config.yaml").write_text(
            "kanban:\n  worktree_roots:\n    " + json.dumps(str(root.resolve())) + ": "
            + json.dumps(str(root / "new-worktrees")) + "\n")
        frozen_target.mkdir(parents=True)
        (frozen_target / "unknown-work").write_text("retain")
        return backup
    monkeypatch.setattr(transfer, "export_board", redirected)
    with pytest.raises(ValueError, match="destination workspace policy"):
        _correct(binding_case, expected=expected)
    with connect_closing(board="alpha") as conn:
        assert kb.get_task(conn, binding_case[4]).project_id == binding_case[2]
    assert (frozen_target / "unknown-work").read_text() == "retain"
