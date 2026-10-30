"""Successful slow workspace resolution cannot transfer a newer attempt's ownership."""
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli import kanban_db_workspace as workspace
from hermes_cli.kanban_db_connect import connect_closing


def _snapshot(conn, task_id):
    return {
        table: [tuple(row) for row in conn.execute(
            f"SELECT * FROM {table} WHERE {column}=? ORDER BY rowid", (task_id,),
        )]
        for table, column in (("tasks", "id"), ("task_runs", "task_id"),
                              ("task_events", "task_id"), ("task_comments", "task_id"))
    }


@pytest.mark.parametrize("collision", [False, True])
@pytest.mark.parametrize("replacement", ["none", "running", "finished", "phase_changed"])
def test_successful_resolution_preserves_replacement(tmp_path, monkeypatch, collision, replacement):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.create_board("workspace-fence")
    observed = {}

    def resolve(task, **_kwargs):
        if replacement == "none":
            observed["original"] = task
            return tmp_path / "stale-workspace", "stale-branch"
        with connect_closing(board="workspace-fence") as other:
            if replacement == "phase_changed":
                with kb.write_txn(other):
                    other.execute("UPDATE tasks SET status='blocked' WHERE id=?", (task.id,))
            else:
                assert kb.reclaim_task(other, task.id, reason="synthetic replacement")
                successor = kb.claim_task(other, task.id, claimer=task.claim_lock)
                assert successor.current_run_id != task.current_run_id
                if replacement == "finished":
                    assert kb.complete_task(other, task.id, summary="synthetic accepted result",
                                            expected_run_id=successor.current_run_id)
            observed["before"] = _snapshot(other, task.id)
        return tmp_path / "stale-workspace", "stale-branch"

    monkeypatch.setattr(workspace, "resolve_dispatch_workspace", resolve)
    monkeypatch.setattr(workspace, "_maybe_emit_scratch_tip", lambda *a: None)
    spawned = []

    def spawn(task, *args, **kwargs):
        assert replacement == "none" and not collision, "stale attempt reached spawn"
        spawned.append(task.id)

    with connect_closing(board="workspace-fence") as conn:
        task_id = kb.create_task(conn, title="Synthetic prepared workspace", assignee="default",
                                 board="workspace-fence")
        if collision:
            owner = kb.create_task(conn, title="Synthetic owner", assignee="default", board="workspace-fence")
            workspace.set_workspace_path(conn, owner, tmp_path / "stale-workspace")
            assert kb.claim_task(conn, owner)
        result = dispatch.dispatch_once(
            conn, board="workspace-fence", task_ids=[task_id], max_in_progress=10,
            spawn_fn=spawn,
        )
        if replacement != "none":
            assert _snapshot(conn, task_id) == observed["before"]
            assert spawned == result.spawned == result.auto_blocked == []
        else:
            task = kb.get_task(conn, task_id)
            assert task.consecutive_failures == 0
            if collision:
                assert task.status == "ready" and task.current_run_id is None
                assert spawned == []
                assert conn.execute("SELECT outcome FROM task_runs WHERE id=?",
                                    (observed["original"].current_run_id,)).fetchone()[0] == "workspace_deferred"
            else:
                assert task.status == "running" and task.claim_lock == observed["original"].claim_lock
                assert task.workspace_path == str(tmp_path / "stale-workspace")
                assert spawned == [task_id]


@pytest.mark.parametrize("existing", ["whitespace", "matching"])
@pytest.mark.parametrize("caller", ["dispatch", "setter"])
def test_allocator_base_normalization_preserves_assignment(tmp_path, monkeypatch, existing, caller):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.create_board("base-normalization")
    ref, sha = "refs/heads/main", "a" * 40
    # This test isolates DB assignment semantics; real Git evidence is covered by
    # test_kanban_worktree_base_assignment's disposable repository.
    monkeypatch.setattr(workspace, "_read_worktree_base", lambda *a, **k: (ref, sha, True))
    stored = (" \t", " \n") if existing == "whitespace" else (f" {ref} ", f" {sha.upper()} ")
    with connect_closing(board="base-normalization") as conn:
        task_id = kb.create_task(conn, title="Synthetic base evidence", assignee="default",
                                 workspace_kind="worktree", workspace_path=str(tmp_path),
                                 board="base-normalization")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET workspace_base_ref=?,workspace_base_sha=? WHERE id=?",
                         (*stored, task_id))
        claimed = kb.claim_task(conn, task_id)
        if caller == "dispatch":
            workspace.persist_dispatch_workspace(conn, claimed, tmp_path / "assigned", "branch")
        else:
            workspace.set_worktree_base(conn, task_id, tmp_path, "branch")
        row = conn.execute("SELECT workspace_base_ref,workspace_base_sha FROM tasks WHERE id=?",
                           (task_id,)).fetchone()
        assert tuple(row) == ((ref, sha) if existing == "whitespace" else stored)
