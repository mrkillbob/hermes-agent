"""Explicit same-ID profile adoption preserves local state and shared ownership."""
import sqlite3
import subprocess

import pytest

from hermes_cli import kanban_db as kb, projects_db
from hermes_cli.kanban_db_connect import connect_closing


@pytest.fixture
def adoption(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "root"
    target = root / "profiles" / "task-orchestrator"
    target.mkdir(parents=True)
    (target / "profile.yaml").write_text("name: task-orchestrator\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(root))
    repo = tmp_path.resolve() / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    with projects_db.connect_closing(root / "projects.db") as conn:
        pid = projects_db.create_project(conn, name="Canonical", primary_path=str(repo),
                                        board_slug="alpha", description="Not adopted")
        projects_db.add_folder(conn, pid, str(tmp_path / "secondary"))
    with projects_db.connect_closing(target / "projects.db") as conn:
        other = projects_db.create_project(conn, name="Local active")
        projects_db.set_active(conn, other)
    kb.create_board("alpha", project_id=pid)
    return dict(source_registry=root / "projects.db", target_registry=target / "projects.db",
                target_profile="task-orchestrator", expected_project_id=pid,
                expected_root=repo, expected_board="alpha")


def adopt(args):
    from hermes_cli.projects_adoption import adopt_canonical_project
    return adopt_canonical_project(**args)


def test_adoption_retains_id_and_local_state_then_enables_strict_creation(adoption, monkeypatch):
    source = adoption["source_registry"]
    target = adoption["target_registry"]
    board = kb.board_metadata_path("alpha")
    before_source, before_board = source.read_bytes(), board.read_bytes()
    with projects_db.connect_closing(target) as conn:
        active = projects_db.get_active_id(conn)
        other_before = projects_db.get_project(conn, active).to_dict()
    assert adopt(adoption)["status"] == "adopted"
    assert adopt(adoption)["status"] == "already_adopted"
    assert source.read_bytes() == before_source and board.read_bytes() == before_board
    with projects_db.connect_closing(target) as conn:
        project = projects_db.get_project(conn, adoption["expected_project_id"])
        assert project.primary_path == str(adoption["expected_root"])
        assert project.board_slug == "alpha" and project.description is None
        assert [folder.path for folder in project.folders] == [project.primary_path]
        assert projects_db.get_active_id(conn) == active
        assert projects_db.get_project(conn, active).to_dict() == other_before
    monkeypatch.setenv("HERMES_HOME", str(target.parent))
    from hermes_cli.kanban_project_create import create_task
    with connect_closing(board="alpha") as conn:
        task = create_task(conn, title="Bound work", project_id=project.id,
                           board="alpha", require_active_project=True)
        assert kb.get_task(conn, task).project_id == project.id


@pytest.mark.parametrize("collision", ["id", "slug", "root", "archived_root", "extra_folder"])
def test_target_conflicts_refuse_without_reconciliation(adoption, collision):
    with projects_db.connect_closing(adoption["source_registry"]) as conn:
        canonical = projects_db.get_project(conn, adoption["expected_project_id"])
    if collision == "extra_folder":
        adopt(adoption)
        with projects_db.connect_closing(adoption["target_registry"]) as conn:
            projects_db.add_folder(conn, canonical.id, str(adoption["expected_root"].parent / "extra"))
    else:
        with projects_db.connect_closing(adoption["target_registry"]) as conn:
            pid = projects_db.create_project(conn, name="Different existing identity",
                  slug=canonical.slug if collision == "slug" else None,
                  primary_path=canonical.primary_path if "root" in collision else None)
            if collision == "id":
                with projects_db.write_txn(conn):
                    conn.execute("UPDATE projects SET id=? WHERE id=?", (canonical.id, pid))
            if collision == "archived_root":
                projects_db.archive_project(conn, pid)
    before = adoption["target_registry"].read_bytes()
    with pytest.raises(ValueError):
        adopt(adoption)
    assert adoption["target_registry"].read_bytes() == before


@pytest.mark.parametrize("drift", ["archive", "metadata", "tombstone", "missing_target"])
def test_retired_registration_and_profile_refuse(adoption, drift):
    from hermes_constants import mark_named_profile_deleted

    def archive():
        with projects_db.connect_closing(adoption["source_registry"]) as conn:
            projects_db.archive_project(conn, adoption["expected_project_id"])
    actions = {
        "archive": archive,
        "metadata": lambda: kb.write_board_metadata("alpha", project_id="p_unknown"),
        "tombstone": lambda: mark_named_profile_deleted(adoption["target_registry"].parent),
        "missing_target": lambda: adoption["target_registry"].unlink(),
    }
    actions[drift]()
    with pytest.raises((ValueError, FileNotFoundError)):
        adopt(adoption)


@pytest.mark.parametrize("drift", ["root", "board", "profile"])
def test_exact_expected_binding_is_required(adoption, drift):
    changes = {
        "root": ("expected_root", adoption["expected_root"].parent),
        "board": ("expected_board", "wrong"),
        "profile": ("target_profile", "Task-Orchestrator"),
    }
    key, value = changes[drift]
    adoption[key] = value
    with pytest.raises(ValueError):
        adopt(adoption)


def test_both_registry_writers_reserved_through_target_insert(adoption, monkeypatch):
    from hermes_cli import projects_adoption
    original = projects_adoption._insert_binding
    observed = []
    def checked(conn, project, folder):
        for path in (adoption["source_registry"], adoption["target_registry"]):
            with sqlite3.connect(path, timeout=0) as contender:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    contender.execute("UPDATE projects SET archived=1")
        observed.append(True)
        return original(conn, project, folder)
    monkeypatch.setattr(projects_adoption, "_insert_binding", checked)
    adopt(adoption)
    assert observed == [True]


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_registry_file_aliases_are_refused(adoption, kind):
    import os

    target = adoption["target_registry"]
    target.unlink()
    if kind == "symlink":
        target.symlink_to(adoption["source_registry"])
    else:
        os.link(adoption["source_registry"], target)
    before = target.read_bytes()
    with pytest.raises(ValueError):
        adopt(adoption)
    assert target.read_bytes() == before


@pytest.mark.parametrize("invalid", ["ghost", "wrong_target"])
def test_target_profile_identity_must_match(adoption, invalid):
    if invalid == "ghost":
        (adoption["target_registry"].parent / "profile.yaml").unlink()
    else:
        adoption["target_profile"] = "another-profile"
    before = adoption["target_registry"].read_bytes()
    with pytest.raises(ValueError):
        adopt(adoption)
    assert adoption["target_registry"].read_bytes() == before


@pytest.mark.parametrize("invalid", ["slug_id", "unknown_schema", "primary_folder"])
def test_source_schema_and_exact_identity_are_required(adoption, invalid):
    if invalid == "slug_id":
        with projects_db.connect_closing(adoption["source_registry"]) as conn:
            adoption["expected_project_id"] = projects_db.get_project(conn, adoption["expected_project_id"]).slug
    else:
        with sqlite3.connect(adoption["source_registry"]) as conn:
            conn.execute("ALTER TABLE projects ADD COLUMN unknown_policy TEXT" if invalid == "unknown_schema"
                         else "UPDATE project_folders SET is_primary=0")
    before = adoption["target_registry"].read_bytes()
    with pytest.raises(ValueError):
        adopt(adoption)
    assert adoption["target_registry"].read_bytes() == before


def test_git_root_replacement_before_commit_rolls_back_target(adoption, monkeypatch):
    from hermes_cli import projects_adoption
    original = projects_adoption._insert_binding
    before = adoption["target_registry"].read_bytes()
    def replaced(conn, project, folder):
        original(conn, project, folder)
        root = adoption["expected_root"]
        root.rename(root.with_name("retained-repo"))
        root.mkdir()
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    monkeypatch.setattr(projects_adoption, "_insert_binding", replaced)
    with pytest.raises(ValueError, match="Git root identity"):
        adopt(adoption)
    assert adoption["target_registry"].read_bytes() == before


def test_failed_primary_folder_insert_rolls_back_project(adoption):
    with sqlite3.connect(adoption["target_registry"]) as conn:
        conn.execute("CREATE TRIGGER reject_folder BEFORE INSERT ON project_folders BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    before = adoption["target_registry"].read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        adopt(adoption)
    assert adoption["target_registry"].read_bytes() == before


def test_profile_retired_before_commit_rolls_back_target(adoption, monkeypatch):
    from hermes_cli import projects_adoption
    from hermes_constants import mark_named_profile_deleted
    original = projects_adoption._insert_binding
    before = adoption["target_registry"].read_bytes()
    def retired(conn, project, folder):
        original(conn, project, folder)
        mark_named_profile_deleted(adoption["target_registry"].parent)
    monkeypatch.setattr(projects_adoption, "_insert_binding", retired)
    with pytest.raises(ValueError, match="live profile"):
        adopt(adoption)
    assert adoption["target_registry"].read_bytes() == before


def test_target_insert_cannot_change_existing_active_selection(adoption):
    with sqlite3.connect(adoption["target_registry"]) as conn:
        conn.execute("CREATE TRIGGER unexpected_activation AFTER INSERT ON projects BEGIN "
                     "UPDATE project_meta SET value=NEW.id WHERE key='active_id'; END")
    before = adoption["target_registry"].read_bytes()
    with pytest.raises(ValueError, match="unrelated target state"):
        adopt(adoption)
    assert adoption["target_registry"].read_bytes() == before


def test_stale_active_pointer_cannot_activate_newly_adopted_project(adoption):
    with projects_db.connect_closing(adoption["target_registry"]) as conn:
        projects_db.set_active(conn, adoption["expected_project_id"])
    before = adoption["target_registry"].read_bytes()
    with pytest.raises(ValueError, match="active"):
        adopt(adoption)
    assert adoption["target_registry"].read_bytes() == before


def test_retirement_after_final_validation_cannot_report_success(adoption, monkeypatch):
    from contextlib import contextmanager
    from hermes_cli import projects_adoption
    from hermes_constants import mark_named_profile_deleted

    original = projects_adoption.write_txn
    @contextmanager
    def retired_at_commit(conn):
        with original(conn):
            yield conn
            db_file = conn.execute("PRAGMA database_list").fetchone()[2]
            if db_file == str(adoption["target_registry"]):
                mark_named_profile_deleted(adoption["target_registry"].parent)
    monkeypatch.setattr(projects_adoption, "write_txn", retired_at_commit)
    with pytest.raises(ValueError, match="profile"):
        adopt(adoption)


@pytest.mark.platforms("posix")
def test_lifecycle_reservation_covers_actual_target_commit(adoption, monkeypatch):
    from contextlib import contextmanager
    import sys
    from hermes_cli import projects_adoption
    from hermes_cli.profiles_lifecycle import _lock_path

    original = projects_adoption.write_txn
    observed = []
    @contextmanager
    def checked_commit(conn):
        with original(conn):
            yield conn
            if conn.execute("PRAGMA database_list").fetchone()[2] == str(adoption["target_registry"]):
                result = subprocess.run([sys.executable, "-c",
                    "import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)",
                    str(_lock_path(adoption["target_registry"].parent))], capture_output=True, check=False)
                assert result.returncode != 0 and b"BlockingIOError" in result.stderr
                observed.append(True)
    monkeypatch.setattr(projects_adoption, "write_txn", checked_commit)
    assert adopt(adoption)["status"] == "adopted"
    assert observed == [True]
