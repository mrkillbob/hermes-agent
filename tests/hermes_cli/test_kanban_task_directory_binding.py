"""Same-project offline work keeps its card and holds without a Git bootstrap."""

import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from hermes_cli import kanban_db as kb, projects_db
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_task_binding import BindingExpectation, correct_task_binding
from tests.hermes_cli.test_kanban_task_binding import binding_case, _revision


@pytest.fixture
def directory_case(request):
    case = request.getfixturevalue("binding_case")
    directory = case[0] / "isolated-operator-task" / case[4]
    directory.mkdir(parents=True)
    return case, directory


def _correct(case, directory, **changes):
    root, roots, project, _new, task_id, row = case
    args = dict(
        source_board="alpha",
        task_id=task_id,
        target_project_id=project,
        expected=BindingExpectation.from_task(row, project_root=str(roots[0]), revision=_revision(case)),
        target_root=str(roots[0]),
        reason="Offline local-only artifact inspection; preserve holds",
        backup_path=str(root / "directory-before.tar.gz"),
        target_directory=str(directory),
    )
    args.update(changes)
    return correct_task_binding(**args)


def _row(task_id):
    with connect_closing(board="alpha") as conn:
        return dict(
            conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        )


def test_directory_correction_preserves_project_card_holds_and_full_history(
    directory_case, monkeypatch
):
    case, directory = directory_case
    from hermes_cli import kanban_db_workspace as workspace

    original_git = workspace._git
    git_calls = []

    def local_git(root, *args, **kwargs):
        assert args[0] not in {"fetch", "pull", "push", "clone", "worktree"}
        git_calls.append(args[0])
        return original_git(root, *args, **kwargs)

    monkeypatch.setattr(workspace, "_git", local_git)
    with connect_closing(board="alpha") as conn:
        kb.add_comment(conn, case[4], "operator", "Offline-only input provenance")
    result = _correct(case, directory)
    after = _row(case[4])
    expected = {
        **case[5],
        "workspace_kind": "dir",
        "workspace_path": str(directory),
        "branch_name": None,
        "workspace_base_ref": None,
        "workspace_base_sha": None,
    }
    assert after == expected
    with connect_closing(board="alpha") as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM task_comments WHERE task_id=?", (case[4],)
            ).fetchone()[0]
            == 2
        )
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='project_binding_corrected'",
                (case[4],),
            ).fetchone()[0]
        )
        assert payload["old_binding"]["workspace_path"] == case[5]["workspace_path"]
        assert payload["old_binding"]["branch_name"] == case[5]["branch_name"]
    with sqlite3.connect(result["recovery_backup"]) as saved:
        saved.row_factory = sqlite3.Row
        assert (
            dict(saved.execute("SELECT * FROM tasks WHERE id=?", (case[4],)).fetchone())
            == case[5]
        )
        assert (
            saved.execute(
                "SELECT COUNT(*) FROM task_comments WHERE task_id=?", (case[4],)
            ).fetchone()[0]
            == 2
        )
    assert not Path(case[5]["workspace_path"]).exists()
    assert after["status"] == "triage" and after["consecutive_failures"] == 2
    assert git_calls and not list(directory.iterdir())


@pytest.mark.parametrize(
    "column,value",
    [
        ("status", "ready"),
        ("claim_lock", "live"),
        ("current_run_id", 42),
        ("worker_pid", 123),
    ],
)
def test_directory_correction_refuses_active_task(directory_case, column, value):
    case, directory = directory_case
    with connect_closing(board="alpha") as conn, kb.write_txn(conn):
        conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, case[4]))
    before = _row(case[4])
    with pytest.raises(ValueError):
        _correct(case, directory)
    assert _row(case[4]) == before


@pytest.mark.parametrize(
    "status,ended_at", [("running", None), ("unknown", 123), ("claimed", 123)]
)
def test_directory_correction_refuses_unsettled_run(directory_case, status, ended_at):
    case, directory = directory_case
    with connect_closing(board="alpha") as conn, kb.write_txn(conn):
        conn.execute(
            "INSERT INTO task_runs(task_id,status,profile,started_at,ended_at) VALUES(?,?,?,?,?)",
            (case[4], status, "fixture", 1, ended_at),
        )
    with pytest.raises(ValueError, match="run"):
        _correct(case, directory)
    assert _row(case[4]) == case[5]


def test_directory_correction_preserves_and_refuses_materialized_old_path(
    directory_case,
):
    case, directory = directory_case
    old = Path(case[5]["workspace_path"])
    old.mkdir(parents=True)
    (old / "untouched").write_text("existing work")
    with pytest.raises(ValueError, match="materialized"):
        _correct(case, directory)
    assert (old / "untouched").read_text() == "existing work"
    assert _row(case[4]) == case[5]


def test_directory_correction_refuses_materialized_old_branch(directory_case):
    case, directory = directory_case
    root = case[1][0]
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "branch", case[5]["branch_name"]],
        check=True,
        capture_output=True,
    )
    with pytest.raises(ValueError, match="materialized"):
        _correct(case, directory)
    assert _row(case[4]) == case[5]


@pytest.mark.parametrize(
    "kind", ["symlink", "repository", "ancestor", "missing", "relative"]
)
def test_directory_correction_refuses_unowned_destination(directory_case, kind):
    case, directory = directory_case
    target = {
        "symlink": directory.parent / "link",
        "repository": case[1][0],
        "ancestor": case[0],
        "missing": directory / "missing",
        "relative": Path("relative-directory"),
    }[kind]
    if kind == "symlink":
        target.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="directory"):
        _correct(case, target)
    assert _row(case[4]) == case[5]


@pytest.mark.parametrize("other_board", ["alpha", "beta"])
def test_directory_correction_refuses_another_card_workspace(
    directory_case, other_board
):
    case, directory = directory_case
    with connect_closing(board=other_board) as conn:
        other = kb.create_task(
            conn,
            title="Existing workspace owner",
            workspace_kind="dir",
            workspace_path=str(directory),
            board=other_board,
        )
    with pytest.raises(ValueError, match="workspace"):
        _correct(case, directory)
    assert _row(case[4]) == case[5]
    with connect_closing(board=other_board) as conn:
        assert kb.get_task(conn, other) is not None


def test_directory_correction_refuses_cross_project_or_board_scope(directory_case):
    case, directory = directory_case
    with pytest.raises(ValueError, match="same"):
        _correct(
            case, directory, target_project_id=case[3], target_root=str(case[1][1])
        )
    assert _row(case[4]) == case[5]


@pytest.mark.parametrize("race", ["history", "owner", "directory", "claim"])
def test_directory_export_race_refuses_cas_drift(directory_case, monkeypatch, race):
    case, directory = directory_case
    from hermes_cli import kanban_transfer as transfer

    original = transfer.export_board

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if race == "directory":
            directory.rename(directory.parent / "previous-directory")
            directory.mkdir()
        else:
            with connect_closing(board="alpha") as conn:
                if race == "history":
                    kb.add_comment(conn, case[4], "operator", "Concurrent evidence")
                elif race == "owner":
                    kb.create_task(
                        conn,
                        title="New workspace owner",
                        workspace_kind="dir",
                        workspace_path=str(directory),
                        board="alpha",
                    )
                else:
                    with kb.write_txn(conn):
                        conn.execute(
                            "UPDATE tasks SET claim_lock=? WHERE id=?",
                            ("new-owner", case[4]),
                        )
        return result

    monkeypatch.setattr(transfer, "export_board", changed)
    with pytest.raises(ValueError):
        _correct(case, directory)
    assert _row(case[4])["workspace_kind"] == "worktree"
    assert (case[0] / "directory-before.tar.gz.source.sqlite3").is_file()


def test_directory_correction_refuses_incomplete_claim_identity(directory_case):
    case, directory = directory_case
    with connect_closing(board="alpha") as conn, kb.write_txn(conn):
        conn.execute("UPDATE tasks SET claim_expires=123 WHERE id=?", (case[4],))
    before = _row(case[4])
    with pytest.raises(ValueError, match="claim"):
        _correct(case, directory)
    assert _row(case[4]) == before


def test_directory_correction_preserves_legacy_null_local_contract(directory_case):
    case, directory = directory_case
    with connect_closing(board="alpha") as conn, kb.write_txn(conn):
        conn.execute("UPDATE tasks SET completion_contract=NULL WHERE id=?", (case[4],))
    _correct(case, directory)
    after = _row(case[4])
    assert after["workspace_kind"] == "dir"
    assert after["workspace_path"] == str(directory)
    assert after["completion_contract"] is None
    assert after["status"] == "triage"


@pytest.mark.parametrize("contract", ["github-pr", "owner/repo", "https://github.com/owner/repo/pull/1", ""])
def test_directory_correction_refuses_repository_completion_contract(directory_case, contract):
    case, directory = directory_case
    with connect_closing(board="alpha") as conn, kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET completion_contract=? WHERE id=?", (contract, case[4])
        )
    with pytest.raises(ValueError, match="local-only"):
        _correct(case, directory)
    assert _row(case[4])["workspace_kind"] == "worktree"


def test_directory_correction_holds_canonical_registry_writer_reservation(
    directory_case, monkeypatch
):
    case, directory = directory_case
    from hermes_cli import kanban_transfer as transfer

    original = transfer.export_board

    def reserved(*args, **kwargs):
        with sqlite3.connect(projects_db.projects_db_path(), timeout=0) as contender:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute(
                    "UPDATE projects SET archived=1 WHERE id=?", (case[2],)
                )
        return original(*args, **kwargs)

    monkeypatch.setattr(transfer, "export_board", reserved)
    _correct(case, directory)
    assert _row(case[4])["workspace_kind"] == "dir"


@pytest.mark.parametrize("failure", ["discovery", "git_error", "timeout"])
def test_directory_correction_refuses_unknown_branch_probe(
    directory_case, monkeypatch, failure
):
    case, directory = directory_case
    from hermes_cli import kanban_db_workspace as workspace

    original = workspace._git

    def uncertain(root, *args, **kwargs):
        if args[0] == "show-ref":
            if failure == "timeout":
                raise subprocess.TimeoutExpired("git", 30)
            code, stderr = {
                "discovery": (1, "filter discovery unavailable"),
                "git_error": (128, "invalid repository"),
            }[failure]
            return subprocess.CompletedProcess([], code, "", stderr)
        return original(root, *args, **kwargs)

    monkeypatch.setattr(workspace, "_git", uncertain)
    with pytest.raises(ValueError, match="branch"):
        _correct(case, directory)
    assert _row(case[4]) == case[5]


@pytest.mark.parametrize("failure", ["discovery", "git_error", "timeout"])
def test_existing_worktree_correction_refuses_unknown_branch_probe(
    directory_case, monkeypatch, failure
):
    case, _directory = directory_case
    from hermes_cli import kanban_db_workspace as workspace
    from tests.hermes_cli.test_kanban_task_binding import _correct as correct_worktree

    original = workspace._git

    def uncertain(root, *args, **kwargs):
        if args[0] == "show-ref":
            if failure == "timeout":
                raise subprocess.TimeoutExpired("git", 30)
            code, stderr = {
                "discovery": (1, "filter discovery unavailable"),
                "git_error": (128, "private diagnostic stderr"),
            }[failure]
            return subprocess.CompletedProcess([], code, "", stderr)
        return original(root, *args, **kwargs)

    monkeypatch.setattr(workspace, "_git", uncertain)
    with pytest.raises(ValueError, match="branch") as caught:
        correct_worktree(case)
    assert "private diagnostic" not in str(caught.value)
    assert _row(case[4]) == case[5]


@pytest.mark.parametrize("repository_kind", ["healthy", "corrupt", "bare"])
def test_directory_destination_rejects_actual_repository_discovery(
    directory_case, repository_kind
):
    case, _directory = directory_case
    target = case[0] / "unrelated-repository"
    args = ["git", "init"]
    if repository_kind == "bare":
        args.append("--bare")
    subprocess.run([*args, str(target)], check=True, capture_output=True)
    if repository_kind == "corrupt":
        (target / ".git" / "config").write_text("[broken config\n")
    before = _row(case[4])
    with pytest.raises(ValueError, match="directory"):
        _correct(case, target)
    assert _row(case[4]) == before


@pytest.mark.parametrize(
    "failure", ["discovery", "config", "timeout", "unexpected_diagnostic"]
)
def test_directory_correction_refuses_uncertain_repository_discovery(
    directory_case, monkeypatch, failure
):
    case, directory = directory_case
    from hermes_cli import _subprocess_compat as compat
    from hermes_cli import kanban_db_workspace as workspace

    original_probe, original_toplevel = (
        compat.bounded_probe_run,
        workspace._git_toplevel,
    )

    def uncertain(argv, **kwargs):
        if len(argv) > 3 and argv[1] == "-C" and Path(argv[2]) == directory:
            if failure == "discovery" and argv[3] == "config":
                return None
            if argv[3] == "rev-parse":
                if failure == "timeout":
                    return None
                code, stderr = {
                    "discovery": (
                        1,
                        "filter discovery unavailable; private diagnostic",
                    ),
                    "config": (128, "fatal: bad config line; private diagnostic"),
                    "unexpected_diagnostic": (
                        128,
                        "fatal: not a git repository (or any of the parent directories): .git\nprivate diagnostic",
                    ),
                }[failure]
                return subprocess.CompletedProcess([], code, "", stderr)
        return original_probe(argv, **kwargs)

    monkeypatch.setattr(compat, "bounded_probe_run", uncertain)
    # Model the original API's collapse of discovery failures to None.
    monkeypatch.setattr(
        workspace,
        "_git_toplevel",
        lambda path: None if Path(path) == directory else original_toplevel(path),
    )
    with pytest.raises(ValueError, match="directory") as caught:
        _correct(case, directory)
    assert "private diagnostic" not in str(caught.value)
    assert _row(case[4]) == case[5]


def test_directory_correction_refuses_parent_repo_hidden_by_inherited_ceiling(
    directory_case, monkeypatch
):
    case, _directory = directory_case
    repository = case[0] / "another-repository"
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    target = repository / "nested-directory"
    target.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(repository))
    hidden = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--absolute-git-dir"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert hidden.returncode == 128 and "not a git repository" in hidden.stderr
    with pytest.raises(ValueError, match="directory"):
        _correct(case, target)
    assert _row(case[4]) == case[5]


def test_isolation_probe_pins_discovery_without_changing_process_environment(
    directory_case, monkeypatch
):
    from hermes_cli import kanban_task_binding as binding
    from hermes_cli import _subprocess_compat as compat
    import os

    case, directory = directory_case
    inherited = {
        "GIT_DIR": str(case[1][0] / ".git"),
        "GIT_COMMON_DIR": str(case[1][0] / ".git"),
        "GIT_WORK_TREE": str(case[1][0]),
        "GIT_OBJECT_DIRECTORY": str(case[0] / "unavailable-objects"),
        "GIT_CEILING_DIRECTORIES": str(case[0]),
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "0",
    }
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    original = compat.bounded_probe_run
    observed = []

    def inspect(argv, **kwargs):
        if argv[1:3] == ["-C", str(directory)] and argv[3] == "rev-parse":
            env = kwargs["env"]
            assert (
                not set(inherited).difference({"GIT_DISCOVERY_ACROSS_FILESYSTEM"})
                & env.keys()
            )
            assert env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] == "1"
            assert env["LC_ALL"] == "C"
            observed.append(True)
        return original(argv, **kwargs)

    monkeypatch.setattr(compat, "bounded_probe_run", inspect)
    assert binding._outside_repository(directory) is True
    assert observed == [True]
    assert {key: os.environ[key] for key in inherited} == inherited


@pytest.mark.parametrize("mode", ["directory", "worktree"])
def test_old_branch_cannot_be_hidden_by_foreign_git_namespace(
    directory_case, monkeypatch, mode
):
    case, directory = directory_case
    canonical = case[1][0]
    subprocess.run(
        [
            "git",
            "-C",
            str(canonical),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(canonical), "branch", case[5]["branch_name"]],
        check=True,
        capture_output=True,
    )
    foreign = case[0] / "foreign-repository"
    subprocess.run(["git", "init", str(foreign)], check=True, capture_output=True)
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(canonical))
    from tests.hermes_cli.test_kanban_task_binding import _correct as correct_worktree

    with pytest.raises(ValueError, match="Git namespace"):
        if mode == "directory":
            _correct(case, directory)
        else:
            correct_worktree(case)
    assert _row(case[4]) == case[5]
    assert not list(case[0].glob("*before.tar.gz*"))


@pytest.mark.parametrize("mode", ["directory", "worktree"])
@pytest.mark.parametrize("value", ["", "synthetic-override"])
@pytest.mark.parametrize(
    "key",
    [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_INDEX_FILE",
        "GIT_GRAFT_FILE",
        "GIT_REPLACE_REF_BASE",
    ],
)
def test_operator_correction_refuses_explicit_git_namespace(
    directory_case, monkeypatch, mode, value, key
):
    import os
    from tests.hermes_cli.test_kanban_task_binding import _correct as correct_worktree

    case, directory = directory_case
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="Git namespace") as caught:
        if mode == "directory":
            _correct(case, directory)
        else:
            correct_worktree(case)
    assert "synthetic-override" not in str(caught.value)
    assert os.environ[key] == value
    assert _row(case[4]) == case[5]
    assert not list(case[0].glob("*before.tar.gz*"))


def test_operator_correction_preserves_non_namespace_git_environment(
    directory_case, monkeypatch
):
    import os

    case, directory = directory_case
    inherited = {"GIT_OPTIONAL_LOCKS": "0", "GIT_ASKPASS": "synthetic-unused-askpass"}
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    _correct(case, directory)
    assert _row(case[4])["workspace_kind"] == "dir"
    assert {key: os.environ[key] for key in inherited} == inherited
