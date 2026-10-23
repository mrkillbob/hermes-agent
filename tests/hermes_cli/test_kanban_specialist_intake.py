"""Specialist intake selects role intent without acquiring workspace authority."""

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import projects_db as pdb
from hermes_cli.kanban_repair_routing import repair_profile_for_task
from hermes_cli.kanban_worker_routing import route_orchestrator_task


@pytest.mark.parametrize("title", [
    "Write a creative brief about dependency health",
    "Explain how to regenerate Python lock files",
    "Design a Local tooling refresh illustration",
    "GitHub Actions pin driftwood poster",
    "Local tooling refreshment menu",
    "Vite lock staleness tutorial",
    "Diagnostic experiment: design a fictional laboratory scene",
    "Diagnostic experiment: compare browser layout measurements",
    "Diagnostic experiment: inspect replay artifacts for another product",
    "Review LunaBot replay samples",
])
def test_incidental_terms_and_unscoped_experiments_stay_in_triage(title):
    assert repair_profile_for_task(title, "A dependency is mentioned as background.") is None


@pytest.mark.parametrize(("title", "owner"), [
    ("Vite lock stale: inspect dependency floors", "dependency-tooling-health-sentinel"),
    ("Regenerate Python lock: inspect security floors", "dependency-tooling-health-sentinel"),
    ("Local tooling refresh: assess parser version", "dependency-tooling-health-sentinel"),
    ("GitHub Actions pin drift: assess workflow revision", "dependency-tooling-health-sentinel"),
    ("Diagnostic experiment: compare LunaBot replay sample ordering", "research-experiment-runner"),
])
def test_explicit_title_intent_wins_over_incidental_inventory(title, owner):
    assert repair_profile_for_task(title, "Quoted inventory: cron profile; state.db; worktree governance") == owner


@pytest.fixture
def intake_board(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for variable in ("HERMES_KANBAN_DB", "HERMES_KANBAN_HOME", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(variable, raising=False)
    kb._INITIALIZED_PATHS.clear()
    board_repo = tmp_path / "board-repo"
    task_repo = tmp_path / "task-repo"
    board_repo.mkdir()
    task_repo.mkdir()
    with pdb.connect_closing() as conn:
        board_project = pdb.create_project(conn, name="Board Project", primary_path=str(board_repo))
        task_project = pdb.create_project(conn, name="Task Project", primary_path=str(task_repo))
    kb.create_board("intake", name="Intake", project_id=board_project)
    return board_repo, task_repo, task_project


@pytest.mark.parametrize(("title", "owner"), [
    ("Local tooling refresh: assess dependency pins", "dependency-tooling-health-sentinel"),
    ("Diagnostic experiment: compare LunaBot replay sample ordering", "research-experiment-runner"),
    ("Market-data freshness: inspect provenance", "market-data-authority-auditor"),
])
@pytest.mark.parametrize("workspace", ["scratch", "explicit-project", "board-project"])
def test_routing_preserves_authoritative_workspace_despite_quoted_paths(intake_board, title, owner, workspace):
    board_repo, task_repo, task_project = intake_board
    options = {"workspace_kind": "scratch"} if workspace == "scratch" else {}
    if workspace == "explicit-project":
        options["project_id"] = task_project
    with kbc.connect(board="intake") as conn:
        task_id = kb.create_task(
            conn, title=title, body=f"Quoted inventory: {board_repo}/notes.md and {task_repo}/notes.md",
            assignee="task-intake-router", board="intake", **options,
        )
        before = kb.get_task(conn, task_id)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        result = kbd.DispatchResult()
        assert route_orchestrator_task(conn, row, dry_run=False, result=result, board="intake") == owner
        after = kb.get_task(conn, task_id)
        assert after.assignee == owner
        assert (after.project_id, after.workspace_kind, after.workspace_path, after.branch_name) == (
            before.project_id, before.workspace_kind, before.workspace_path, before.branch_name,
        )
        assert "workspace_anchored_to_project" not in [event.kind for event in kb.list_events(conn, task_id)]


def test_explicit_profile_is_preserved_for_new_title_family(intake_board):
    with kbc.connect(board="intake") as conn:
        task_id = kb.create_task(conn, title="Local tooling refresh: assess dependency pins",
                                 assignee="coding-expert", board="intake")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        result = kbd.DispatchResult()
        assert route_orchestrator_task(conn, row, dry_run=False, result=result, board="intake") == "coding-expert"
        assert kb.get_task(conn, task_id).assignee == "coding-expert"
        assert result.routed_to_specialist == []


def test_explicit_profile_bypasses_operational_blocker_routing(intake_board):
    with kbc.connect(board="intake") as conn:
        task_id = kb.create_task(conn, title="GitHub Actions pin drift: inspect", body="Usage limits were exceeded.",
                                 assignee="coding-expert", board="intake")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        result = kbd.DispatchResult()
        assert route_orchestrator_task(conn, row, dry_run=False, result=result, board="intake") == "coding-expert"
        assert kb.get_task(conn, task_id).assignee == "coding-expert"


@pytest.mark.parametrize("workspace", ["scratch", "explicit-project", "board-project"])
def test_blocker_routing_preserves_authoritative_workspace(intake_board, workspace):
    board_repo, task_repo, task_project = intake_board
    options = {"workspace_kind": "scratch"} if workspace == "scratch" else {}
    if workspace == "explicit-project":
        options["project_id"] = task_project
    with kbc.connect(board="intake") as conn:
        task_id = kb.create_task(
            conn,
            title="GitHub Actions pin drift: inspect",
            body=f"Usage limits were exceeded.\nQuoted inventory: {board_repo}/notes.md and {task_repo}/notes.md",
            assignee="task-intake-router",
            board="intake",
            **options,
        )
        before = kb.get_task(conn, task_id)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        result = kbd.DispatchResult()
        assert route_orchestrator_task(conn, row, dry_run=False, result=result, board="intake") == "hermes-maintenance-steward"
        after = kb.get_task(conn, task_id)
        assert after.assignee == "hermes-maintenance-steward"
        assert (after.project_id, after.workspace_kind, after.workspace_path, after.branch_name) == (
            before.project_id, before.workspace_kind, before.workspace_path, before.branch_name,
        )
