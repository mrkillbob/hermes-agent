"""Discovery respects current project lifecycle before reserving work or evidence paths."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from hermes_cli import projects_db
from scripts import federation_discovery as discovery


def test_project_lifecycle_filter_preserves_capacity_for_current_board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with projects_db.connect_closing() as conn:
        retired = projects_db.create_project(conn, name="Retired", slug="retired")
        projects_db.archive_project(conn, retired)
        active = projects_db.create_project(conn, name="Current", slug="current", board_slug="default")
        projects_db.create_project(conn, name="Other", slug="other", board_slug="other-board")
    spec = {
        "board": "default", "max_active": 3, "max_dispatches": 1,
        "departments": [
            {"id": name, "project": project, "title": name, "assignee": "researcher"}
            for name, project in [("retired", "retired"), ("missing", "missing"),
                                  ("wrong", "other"), ("current", active)]
        ],
    }
    filtered, skipped = discovery.eligible_project_spec(spec)
    plan = discovery.plan_discovery(filtered, [], "2026-10-22")
    assert [item["id"] for item in plan] == ["current"]
    assert {item["department"]: item["reason"] for item in skipped} == {
        "retired": "project_archived", "missing": "project_missing", "wrong": "project_board_mismatch",
    }
    assert len(spec["departments"]) == 4
    with projects_db.connect_closing() as conn:
        assert projects_db.get_project(conn, retired).archived


def test_unavailable_project_catalog_creates_no_workspace_or_task(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    config = tmp_path / "source/configs/federation"
    config.mkdir(parents=True)
    (config / "discovery.json").write_text(json.dumps({"board": "default", "departments": []}))
    monkeypatch.setattr(discovery, "__file__", str(tmp_path / "source/scripts/federation_discovery.py"))
    monkeypatch.setattr(discovery.sys, "argv", ["discovery", "--hermes", "unused", "--apply"])
    calls = []
    monkeypatch.setattr(discovery, "run", lambda *args: calls.append(args))
    discovery.main()
    assert json.loads(capsys.readouterr().out) == {"status": "idle", "reason": "project_registry_unavailable"}
    assert calls == []
    assert not projects_db.projects_db_path().exists()


def test_catalog_read_is_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with projects_db.connect_closing() as conn:
        projects_db.create_project(conn, name="Current", slug="current")
    path = projects_db.projects_db_path()
    before = path.read_bytes()
    spec = {"board": "default", "departments": [{"id": "current", "project": "current"}]}
    filtered, skipped = discovery.eligible_project_spec(spec)
    assert skipped == [] and filtered['departments'][0]['project'] == 'current'
    assert filtered['departments'][0]['_project_id'].startswith('p_')
    assert path.read_bytes() == before


@pytest.mark.parametrize('during_listing', [False, True])
def test_apply_does_not_materialize_archived_project_evidence(tmp_path, monkeypatch, capsys, during_listing):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    with projects_db.connect_closing() as conn:
        retired = projects_db.create_project(conn, name="Retired", slug="retired")
        if not during_listing:
            projects_db.archive_project(conn, retired)
    config = tmp_path / "source/configs/federation"
    config.mkdir(parents=True)
    (config / "discovery.json").write_text(json.dumps({
        "board": "default", "max_active": 2, "max_dispatches": 1,
        "departments": [{"id": "retired", "project": "retired", "title": "Retired", "assignee": "researcher"}],
    }))
    from hermes_cli import kanban_db

    board = tmp_path / "board.db"
    from hermes_cli.kanban_db_connect import connect
    with connect(board):
        pass
    monkeypatch.setattr(kanban_db, "kanban_db_path", lambda _: board)
    monkeypatch.setattr(discovery, "__file__", str(tmp_path / "source/scripts/federation_discovery.py"))
    monkeypatch.setattr(discovery.sys, "argv", ["discovery", "--hermes", "unused", "--apply"])
    calls = []

    def command(_hermes, *args):
        calls.append(args)
        assert "create" not in args
        if during_listing:
            with projects_db.connect_closing() as conn:
                projects_db.archive_project(conn, retired)
        return []

    monkeypatch.setattr(discovery, "run", command)
    discovery.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "idle" and result["receipts"] == []
    assert result["skipped_projects"] == [
        {"department": "retired", "project": "retired", "reason": "project_archived"},
    ]
    assert len(calls) == 1
    assert not list((tmp_path / "home").rglob("discovery-evidence.json"))


def test_catalog_follows_the_calling_profile_not_another_registry(tmp_path, monkeypatch):
    active_home, retired_home = tmp_path / "active", tmp_path / "retired"
    spec = {"board": "default", "departments": [{"id": "shared", "project": "shared"}]}
    for home in [active_home, retired_home]:
        monkeypatch.setenv("HERMES_HOME", str(home))
        with projects_db.connect_closing() as conn:
            project = projects_db.create_project(conn, name="Shared", slug="shared")
            if home == retired_home:
                projects_db.archive_project(conn, project)
    assert discovery.eligible_project_spec(spec)[0]["departments"] == []
    monkeypatch.setenv("HERMES_HOME", str(active_home))
    filtered, skipped = discovery.eligible_project_spec(spec)
    assert skipped == [] and filtered['departments'][0]['project'] == 'shared'


def test_direct_script_starts_without_ambient_pythonpath(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["HERMES_HOME"] = str(tmp_path / "unregistered-profile")
    result = subprocess.run(
        [sys.executable, str(Path(discovery.__file__).resolve()), "--hermes", "unused"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "idle", "reason": "project_registry_unavailable"}


@pytest.mark.parametrize('change,reason', [('replace', 'project_missing'), ('rebind', 'project_board_mismatch')])
def test_project_recheck_does_not_follow_reused_slug_or_new_board(tmp_path, monkeypatch, change, reason):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    with projects_db.connect_closing() as conn:
        original = projects_db.create_project(conn, name='Current', slug='current', board_slug='default')
    filtered, _ = discovery.eligible_project_spec({
        'board': 'default', 'max_active': 2, 'max_dispatches': 1,
        'departments': [{'id': 'current', 'project': 'current', 'title': 'Current', 'assignee': 'researcher'}],
    })
    department = discovery.plan_discovery(filtered, [], '2026-10-22')[0]
    with projects_db.connect_closing() as conn:
        if change == 'replace':
            conn.execute('DELETE FROM projects WHERE id=?', (original,))
            conn.commit()
            replacement = projects_db.create_project(conn, name='Replacement', slug='current')
            assert replacement != original
        else:
            projects_db.update_project(conn, original, board_slug='different-board')
    assert department['_project_id'] == original
    assert discovery.current_project_reason(department) == reason


def test_legacy_catalog_without_optional_board_binding_is_readonly(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    path = tmp_path / 'projects.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,slug TEXT,archived INTEGER)')
        conn.execute("INSERT INTO projects VALUES('p_12345678','legacy',0)")
    before = path.read_bytes()
    filtered, skipped = discovery.eligible_project_spec({
        'board': 'default', 'departments': [{'id': 'legacy', 'project': 'legacy', 'title': 'Legacy', 'assignee': 'researcher'}],
        'max_active': 1, 'max_dispatches': 1,
    })
    assert skipped == []
    department = discovery.plan_discovery(filtered, [], '2026-10-22')[0]
    assert discovery.current_project_reason(department) is None
    assert path.read_bytes() == before


def test_id_selector_uses_slug_board_mapping_through_planning(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    with projects_db.connect_closing() as conn:
        project = projects_db.create_project(conn, name='Luna', slug='lunabot', board_slug='luna-board')
    filtered, skipped = discovery.eligible_project_spec({
        'board': 'default', 'project_boards': {'lunabot': 'luna-board'},
        'departments': [{'id': 'knowledge', 'project': project, 'title': 'Knowledge', 'assignee': 'librarian'}],
        'max_active': 1, 'max_dispatches': 1,
    })
    assert skipped == []
    assert discovery.plan_discovery(filtered, [], '2026-10-22')[0]['board'] == 'luna-board'


def test_retirement_during_evidence_preparation_cannot_create_a_card(tmp_path, monkeypatch):
    from hermes_cli import kanban_db
    from hermes_cli.kanban_db_connect import connect_closing
    from hermes_cli.kanban_project_create import create_task

    home = tmp_path / 'home'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    with projects_db.connect_closing() as registry:
        project = projects_db.create_project(registry, name='Current', slug='current', board_slug='default')
    config = tmp_path / 'source/configs/federation'
    config.mkdir(parents=True)
    (config / 'discovery.json').write_text(json.dumps({
        'board': 'default', 'max_active': 1, 'max_dispatches': 1, 'instructions': 'Fixture evidence only',
        'departments': [{'id': 'current', 'project': 'current', 'title': 'Discovery', 'assignee': 'researcher'}],
    }))
    board = tmp_path / 'board.db'
    with connect_closing(board):
        pass
    monkeypatch.setattr(kanban_db, 'kanban_db_path', lambda _: board)
    monkeypatch.setattr(discovery, '__file__', str(tmp_path / 'source/scripts/federation_discovery.py'))
    monkeypatch.setattr(discovery.sys, 'argv', ['discovery', '--hermes', 'unused', '--apply'])

    def retire_after_evidence(*_):
        with projects_db.connect_closing() as registry:
            projects_db.archive_project(registry, project)

    def command(_hermes, *args):
        if 'list' in args:
            return []
        with connect_closing(board) as conn:
            task_id = create_task(conn, title='Discovery', project_id=args[args.index('--project') + 1],
                                  board='default', require_active_project='--require-active-project' in args)
            return {'id': task_id, 'project_id': kanban_db.get_task(conn, task_id).project_id}

    monkeypatch.setattr(discovery, 'seed_discovery_evidence', retire_after_evidence)
    monkeypatch.setattr(discovery, 'run', command)
    with pytest.raises(ValueError, match='current canonical project'):
        discovery.main()
    with connect_closing(board) as conn:
        assert kanban_db.list_tasks(conn) == []
