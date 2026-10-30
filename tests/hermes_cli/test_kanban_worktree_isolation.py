"""Per-task worktree isolation for decompose siblings.

Decompose children used to inherit the root's literal ``workspace_path``,
so every sibling of a worktree-kind root pointed at the SAME checkout —
and ``_resolve_worktree_workspace``'s existing-checkout shortcut reused it
on whatever branch was there, letting sibling workers run concurrently in
one directory on one branch (cross-task provenance corruption, no lock).

Two-part fix under test:
- ``decompose_triage_task`` leaves worktree children's ``workspace_path``
  unset so each child materializes its own ``<repo>/.worktrees/<child-id>``.
- ``_resolve_worktree_workspace`` falls back to a fresh per-task worktree
  when the requested path is occupied by another task's branch (heals
  pre-existing rows that still carry a shared path).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_graph import decompose_triage_task
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_workspace as kbw


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git", "-C", str(cwd),
            "-c", "user.name=Test User",
            "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        check=True, capture_output=True, text=True,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True, capture_output=True, text=True,
    )
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _add_worktree(repo: Path, target: Path, branch: str) -> Path:
    _git(repo, "worktree", "add", str(target), "-b", branch, "HEAD")
    return target


def test_decompose_worktree_children_get_own_workspace(kanban_home):
    with kbc.connect() as conn:
        root = kb.create_task(conn, title="build the feature", triage=True)
        conn.execute(
            "UPDATE tasks SET workspace_kind='worktree', "
            "workspace_path='/repo/.worktrees/root' WHERE id = ?",
            (root,),
        )
        conn.commit()

        child_ids = decompose_triage_task(
            conn,
            root,
            root_assignee="orchestrator",
            children=[
                {"title": "spec it", "assignee": "alice", "parents": []},
                {"title": "implement it", "assignee": "bob", "parents": [0]},
            ],
            author="decomposer",
        )
        assert child_ids is not None and len(child_ids) == 2

        for cid in child_ids:
            row = conn.execute(
                "SELECT workspace_kind, workspace_path FROM tasks WHERE id = ?",
                (cid,),
            ).fetchone()
            assert row["workspace_kind"] == "worktree"
            # Each child resolves its own <repo>/.worktrees/<child-id> at
            # dispatch; the root's literal path must never be shared.
            assert row["workspace_path"] is None




@pytest.mark.parametrize("placement", ["default", "configured", "external_sibling"])
def test_resolve_worktree_falls_back_when_path_occupied(kanban_home, tmp_path, placement):
    repo = _make_repo(tmp_path)
    workspace_root = repo / ".worktrees"
    if placement != "default":
        workspace_root = tmp_path / "configured-workspaces"
        (kanban_home / "config.yaml").write_text(json.dumps({
            "kanban": {"worktree_roots": {str(repo.resolve()): str(workspace_root)}}
        }))
    sibling_root = workspace_root if placement == "external_sibling" else repo / ".worktrees"
    occupied = _add_worktree(repo, sibling_root / "sibling", "wt/sibling")
    (occupied / "sibling-work.txt").write_text("unfinished sibling work\n")

    with kbc.connect() as conn:
        tid = kb.create_task(
            conn,
            title="second sibling",
            workspace_kind="worktree",
            workspace_path=str(occupied),  # inherited shared/stale path
        )
        task = kb.get_task(conn, tid)

    workspace, branch = kbw._resolve_worktree_workspace(task)
    assert workspace == (workspace_root / tid).resolve()
    assert branch == f"wt/{tid}"
    # The sibling's checkout is untouched, still on its own branch.
    assert (occupied / "README.md").exists()
    head = subprocess.run(
        ["git", "-C", str(occupied), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == "wt/sibling"
    assert (occupied / "sibling-work.txt").read_text() == "unfinished sibling work\n"
    assert kbw._resolve_worktree_workspace(task) == (workspace, branch)
    if placement != "default":
        assert not (repo / ".worktrees" / tid).exists()


def test_occupied_worktree_rejects_invalid_location_policy(kanban_home, tmp_path):
    repo = _make_repo(tmp_path)
    occupied = _add_worktree(repo, repo / ".worktrees" / "sibling", "wt/sibling")
    with kbc.connect() as conn:
        tid = kb.create_task(
            conn, title="second sibling", workspace_kind="worktree", workspace_path=str(occupied),
        )
        task = kb.get_task(conn, tid)
    (kanban_home / "config.yaml").write_text(json.dumps({
        "kanban": {"worktree_roots": {str(repo.resolve()): "relative-location"}}
    }))

    with pytest.raises(ValueError, match="absolute separate directory"):
        kbw._resolve_worktree_workspace(task)
    assert not (repo / ".worktrees" / tid).exists()
    assert (occupied / "README.md").read_text() == "base\n"


@pytest.mark.platforms("linux", "macos")
def test_configured_fallback_alias_preserves_occupied_worktree(kanban_home, tmp_path):
    repo = _make_repo(tmp_path)
    occupied = _add_worktree(repo, repo / ".worktrees" / "sibling", "wt/sibling")
    with kbc.connect() as conn:
        tid = kb.create_task(
            conn, title="second sibling", workspace_kind="worktree", workspace_path=str(occupied),
        )
        task = kb.get_task(conn, tid)
    root = tmp_path / "configured-workspaces"
    root.mkdir()
    alias = root / tid
    alias.symlink_to(occupied, target_is_directory=True)
    (kanban_home / "config.yaml").write_text(json.dumps({
        "kanban": {"worktree_roots": {str(repo.resolve()): str(root)}}
    }))

    with pytest.raises(ValueError, match="occupied"):
        kbw._resolve_worktree_workspace(task)
    assert alias.is_symlink()
    assert (occupied / "README.md").read_text() == "base\n"
    assert not (repo / ".worktrees" / tid).exists()





@pytest.mark.parametrize("entry", ["anchor", "fallback"])
@pytest.mark.parametrize("detached", [False, True])
def test_canonical_target_never_reuses_another_assignment(
    kanban_home, tmp_path, entry, detached, monkeypatch,
):
    repo = _make_repo(tmp_path)
    root = tmp_path / "external-workspaces"
    (kanban_home / "config.yaml").write_text(json.dumps({
        "kanban": {"worktree_roots": {str(repo.resolve()): str(root)}}
    }))
    requested = repo
    if entry == "fallback":
        requested = _add_worktree(repo, tmp_path / "sibling", "wt/sibling")
    with kbc.connect() as conn:
        tid = kb.create_task(
            conn, title="isolated assignment", workspace_kind="worktree",
            workspace_path=str(requested),
        )
        task = kb.get_task(conn, tid)
    occupied = _add_worktree(repo, root / tid, "wt/foreign")
    if detached:
        _git(occupied, "checkout", "--detach")
    (occupied / "unfinished.txt").write_text("foreign unfinished work\n")
    from hermes_cli import worktree_environment
    bootstraps = []
    monkeypatch.setattr(worktree_environment, "bootstrap_worktree_environments",
                        lambda *args, **kwargs: bootstraps.append(args))

    with pytest.raises(ValueError, match="occupied"):
        kbw._resolve_worktree_workspace(task)

    assert not bootstraps
    assert (occupied / "unfinished.txt").read_text() == "foreign unfinished work\n"
    actual = subprocess.run(
        ["git", "-C", str(occupied), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert actual == ("" if detached else "wt/foreign")
    metadata = subprocess.run(
        ["git", "-C", str(repo), "config", "--get",
         f"branch.wt/{tid}.hermes-kanban-base-sha"],
        capture_output=True, text=True, check=False,
    )
    assert metadata.returncode == 1

@pytest.mark.parametrize("entry", ["resolver", "allocator"])
def test_worktree_subdirectory_is_not_an_assignment_root(kanban_home, tmp_path, entry):
    repo = _make_repo(tmp_path)
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="exact assignment root", workspace_kind="worktree")
        branch = f"wt/{tid}"
        checkout = _add_worktree(repo, tmp_path / "checkout", branch)
        nested = checkout / "nested"
        nested.mkdir()
        (nested / "work.txt").write_text("preserve\n")
        kbw.set_workspace_path(conn, tid, nested)
        task = kb.get_task(conn, tid)
    with pytest.raises(ValueError, match="checkout root"):
        if entry == "resolver":
            kbw._resolve_worktree_workspace(task)
        else:
            kbw._ensure_git_worktree(repo, nested, branch)
    assert (nested / "work.txt").read_text() == "preserve\n"
