"""The managed worker executor must carry the control plane root the dispatcher bound."""

import os

from gateway.session_kanban import bind_worker_context
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing


def test_bound_worker_environment_names_the_control_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_CONTROL_HOME", raising=False)
    kb.init_db()
    path = kb.kanban_db_path()
    with connect_closing(path) as conn:
        task_id = kb.create_task(conn, title="repair", assignee="default")
        task = kb.claim_task(conn, task_id, claimer="private-host:owner")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cwd = os.getcwd()
    monkeypatch.setattr("gateway.session_kanban._watch_submitter", lambda *a, **k: None)
    context = {"db": str(path), "task_id": task_id, "run_id": task.current_run_id, "claim_lock": task.claim_lock,
               "board": "default", "workspace": str(workspace), "workspaces_root": str(tmp_path / "wsroot"),
               "profile": "default", "branch": None, "tenant": None, "goal_mode": False,
               "max_runtime_seconds": None}
    frame = {"policy": {"source": "kanban", "kanban_json": __import__("json").dumps(context)}}
    try:
        bind_worker_context(frame)
        assert os.environ["HERMES_CONTROL_HOME"] == str(tmp_path)
    finally:
        os.chdir(cwd)
        for key in [k for k in os.environ if k.startswith("HERMES_KANBAN_")] + ["HERMES_CONTROL_HOME"]:
            os.environ.pop(key, None)
