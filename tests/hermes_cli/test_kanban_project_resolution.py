"""Project-scoped creation must not silently become an unbound card."""
import pytest

from hermes_cli import kanban_db as kb, projects_db
from hermes_cli.kanban_db_connect import connect_closing


@pytest.mark.parametrize("source", ["explicit", "board", "unreadable"])
def test_unresolved_project_refuses_before_card_or_event_insert(tmp_path, monkeypatch, source):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.create_board("bound", project_id="p_missing")
    kwargs = {} if source == "board" else {"project_id": "p_missing"}
    if source == "unreadable":
        projects_db.projects_db_path().write_bytes(b"not a SQLite database")
    with connect_closing(board="bound") as conn:
        before = conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
        with pytest.raises(ValueError, match="project"):
            kb.create_task(conn, title="Scoped work", board="bound", triage=True, **kwargs)
        assert kb.list_tasks(conn) == []
        assert conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == before


@pytest.mark.parametrize("matching", [True, False])
def test_cross_profile_parent_fallback_keeps_scope_or_refuses(tmp_path, monkeypatch, matching):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    kb._INITIALIZED_PATHS.clear()
    with projects_db.connect_closing() as registry:
        project = projects_db.create_project(registry, name="Owned", primary_path=str(tmp_path / "repo"))
    kb.create_board("bound", project_id=project)
    with connect_closing(board="bound") as conn:
        parent = kb.create_task(conn, title="Parent", project_id=project, board="bound", triage=True)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "worker-profile"))
        kwargs = dict(title="Child", project_id=project if matching else "p_foreign",
                      project_source_task_id=parent, board="bound", triage=True)
        if matching:
            child = kb.create_task(conn, **kwargs)
            task = kb.get_task(conn, child)
            assert task.project_id == project and task.status == "triage"
            assert task.workspace_path != kb.get_task(conn, parent).workspace_path
        else:
            with pytest.raises(ValueError, match="project"):
                kb.create_task(conn, **kwargs)
            assert [task.id for task in kb.list_tasks(conn)] == [parent]
