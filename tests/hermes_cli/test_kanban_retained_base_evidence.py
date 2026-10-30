"""Retained assignment records may recover missing Git keys without inventing a base."""
import subprocess
from pathlib import Path

import pytest


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
         "-C", str(repo), *args], check=check, capture_output=True, text=True,
        timeout=20, stdin=subprocess.DEVNULL,
    )


def _snapshot(conn, task_id):
    return {table: [tuple(row) for row in conn.execute(
        f"SELECT * FROM {table} WHERE {key}=? ORDER BY rowid", (task_id,),
    )] for table, key in [("tasks", "id"), ("task_runs", "task_id"), ("task_events", "task_id")]}


@pytest.fixture
def retained_case(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "assigned")
    assigned = _git(repo, "rev-parse", "HEAD").stdout.strip()
    target = tmp_path / "work"
    branch = "wt/retained"
    _git(repo, "worktree", "add", "-q", "-b", branch, str(target), assigned)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "newer-main")
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != assigned
    kb.create_board("retained")
    with connect_closing(board="retained") as conn:
        tid = kb.create_task(conn, title="Synthetic retained assignment", assignee="default",
                             workspace_kind="worktree", workspace_path=str(target), branch_name=branch)
        conn.execute("UPDATE tasks SET workspace_base_ref=?,workspace_base_sha=? WHERE id=?",
                     ("refs/heads/main", assigned, tid))
        conn.commit()
        task = kb.claim_task(conn, tid)
        yield conn, repo, target, branch, assigned, task


@pytest.mark.parametrize("retained_key", ["none", "ref", "sha"])
def test_retained_assignment_recovers_missing_keys_without_rewriting_config(retained_case, retained_key):
    from hermes_cli import kanban_db_workspace as workspace

    conn, repo, target, branch, assigned, task = retained_case
    if retained_key != "none":
        value = "refs/heads/main" if retained_key == "ref" else assigned
        _git(repo, "config", f"branch.{branch}.hermes-kanban-base-{retained_key}", value)
    config = repo / ".git/config"
    before = config.read_bytes()
    workspace.persist_dispatch_workspace(conn, task, target, branch)
    row = conn.execute("SELECT workspace_base_ref,workspace_base_sha FROM tasks WHERE id=?", (task.id,)).fetchone()
    assert tuple(row) == ("refs/heads/main", assigned)
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == assigned
    assert config.read_bytes() == before


@pytest.mark.parametrize("missing", ["ref", "sha", "both"])
def test_missing_prior_record_still_refuses(retained_case, missing):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_workspace as workspace

    conn, _repo, target, branch, _assigned, task = retained_case
    for field in (["ref", "sha"] if missing == "both" else [missing]):
        conn.execute(f"UPDATE tasks SET workspace_base_{field}=NULL WHERE id=?", (task.id,))
    conn.commit()
    task = kb.get_task(conn, task.id)
    before = _snapshot(conn, task.id)
    with pytest.raises(RuntimeError):
        workspace.persist_dispatch_workspace(conn, task, target, branch)
    assert _snapshot(conn, task.id) == before


@pytest.mark.parametrize("conflict", ["ref", "sha", "branch", "commit", "lineage"])
def test_retained_evidence_refuses_conflicts_without_writes(retained_case, conflict):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_workspace as workspace

    conn, repo, target, branch, _assigned, task = retained_case
    if conflict in ("ref", "sha"):
        value = "refs/heads/other" if conflict == "ref" else "f" * 40
        _git(repo, "config", f"branch.{branch}.hermes-kanban-base-{conflict}", value)
    elif conflict == "branch":
        _git(target, "checkout", "-q", "-b", "other")
    elif conflict == "lineage":
        _git(target, "checkout", "-q", "--orphan", "unrelated")
        _git(target, "commit", "-q", "--allow-empty", "-m", "unrelated")
        unrelated = _git(target, "rev-parse", "HEAD").stdout.strip()
        _git(target, "checkout", "-q", branch)
        _git(target, "reset", "-q", "--hard", unrelated)
    else:
        conn.execute("UPDATE tasks SET workspace_base_sha=? WHERE id=?", ("f" * 40, task.id))
        conn.commit()
        task = kb.get_task(conn, task.id)
    before = _snapshot(conn, task.id)
    config = (repo / ".git/config").read_bytes()
    with pytest.raises(RuntimeError):
        workspace.persist_dispatch_workspace(conn, task, target, branch)
    assert _snapshot(conn, task.id) == before
    assert (repo / ".git/config").read_bytes() == config


def test_normalized_retained_pair_is_not_rewritten(retained_case):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_workspace as workspace

    conn, repo, target, branch, assigned, task = retained_case
    ref, sha = " refs/heads/main ", " " + assigned.upper() + " "
    conn.execute("UPDATE tasks SET workspace_base_ref=?,workspace_base_sha=? WHERE id=?", (ref, sha, task.id))
    conn.commit()
    task = kb.get_task(conn, task.id)
    before = _snapshot(conn, task.id)
    config = (repo / ".git/config").read_bytes()
    workspace.persist_dispatch_workspace(conn, task, target, branch)
    assert _snapshot(conn, task.id) == before
    assert (repo / ".git/config").read_bytes() == config


@pytest.mark.parametrize("change", ["successor", "base", "clear_ref", "clear_sha", "clear_both", "malformed_config"])
def test_retained_recovery_cannot_override_changed_authority(retained_case, change):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_workspace as workspace

    conn, repo, target, branch, _assigned, task = retained_case
    if change == "successor":
        assert kb.reclaim_task(conn, task.id, reason="synthetic replacement")
        assert kb.claim_task(conn, task.id).current_run_id != task.current_run_id
    elif change == "base":
        newer = _git(repo, "rev-parse", "main").stdout.strip()
        conn.execute("UPDATE tasks SET workspace_base_sha=? WHERE id=?", (newer, task.id))
        conn.commit()
    elif change.startswith("clear_"):
        fields = ["ref", "sha"] if change == "clear_both" else [change.removeprefix("clear_")]
        for field in fields:
            conn.execute(f"UPDATE tasks SET workspace_base_{field}=NULL WHERE id=?", (task.id,))
        conn.commit()
    else:
        (repo / ".git/config").write_text("[invalid\n")
    before = _snapshot(conn, task.id)
    config = (repo / ".git/config").read_bytes()
    with pytest.raises(RuntimeError):
        workspace.persist_dispatch_workspace(conn, task, target, branch)
    assert _snapshot(conn, task.id) == before
    assert (repo / ".git/config").read_bytes() == config


def test_first_assignment_still_uses_complete_allocator_evidence(retained_case):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_workspace as workspace

    conn, repo, target, branch, assigned, task = retained_case
    _git(repo, "config", f"branch.{branch}.hermes-kanban-base-ref", "refs/heads/main")
    _git(repo, "config", f"branch.{branch}.hermes-kanban-base-sha", assigned)
    conn.execute("UPDATE tasks SET workspace_base_ref=NULL,workspace_base_sha=NULL WHERE id=?", (task.id,))
    conn.commit()
    task = kb.get_task(conn, task.id)
    workspace.persist_dispatch_workspace(conn, task, target, branch)
    row = conn.execute("SELECT workspace_base_ref,workspace_base_sha FROM tasks WHERE id=?", (task.id,)).fetchone()
    assert tuple(row) == ("refs/heads/main", assigned)
