"""A last unrelated borrower must release a terminal owner's deferred workspace."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_workspace as kbw


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / ".hermes"
    path.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return path


@pytest.fixture
def repo(tmp_path):
    origin, project = tmp_path / "origin.git", tmp_path / "project"
    _git("init", "--bare", str(origin))
    _git("clone", str(origin), str(project))
    _git("-C", str(project), "config", "user.email", "test@example.com")
    _git("-C", str(project), "config", "user.name", "Test")
    (project / "README.md").write_text("synthetic\n", encoding="utf-8")
    _git("-C", str(project), "add", "README.md")
    _git("-C", str(project), "commit", "-m", "initial")
    _git("-C", str(project), "push", "origin", "HEAD")
    return project


def _owner(conn, kind, repo):
    tid = kb.create_task(conn, title="workspace owner", workspace_kind=kind)
    branch = f"wt/{tid}" if kind == "worktree" else None
    if branch:
        path = repo / ".worktrees" / tid
        kbw._ensure_git_worktree(repo, path, branch)
    else:
        path = Path(kbw.resolve_workspace(kb.get_task(conn, tid)))
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET workspace_path=?, branch_name=? WHERE id=?",
            (str(path), branch, tid),
        )
    return tid, path


def _borrower(conn, path):
    return kb.create_task(
        conn, title="unrelated borrower", workspace_kind="dir", workspace_path=str(path),
    )


@pytest.mark.parametrize("kind", ["scratch", "worktree"])
@pytest.mark.parametrize("other_board", [False, True])
@pytest.mark.parametrize("finish", ["archive", "complete"])
def test_last_unrelated_borrower_releases_deferred_owner(home, repo, kind, other_board, finish):
    if other_board:
        kb.create_board("consumer")
    with kbc.connect_closing() as owner_conn:
        owner, path = _owner(owner_conn, kind, repo)
        with kbc.connect_closing(board="consumer" if other_board else None) as conn:
            borrower = _borrower(conn, path)
            assert kb.archive_task(owner_conn, owner)
            assert path.is_dir()
            if finish == "archive":
                assert kb.archive_task(conn, borrower)
            else:
                assert kb.complete_task(conn, borrower, summary="borrower finished")
            assert not path.exists()


@pytest.mark.parametrize("kind", ["scratch", "worktree"])
def test_remaining_unrelated_consumer_preserved(home, repo, kind):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, kind, repo)
        first, last = _borrower(conn, path), _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        assert kb.archive_task(conn, first)
        assert path.is_dir()
        assert kb.archive_task(conn, last)
        assert not path.exists()


def test_active_child_preserved(home, repo):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "scratch", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        kb.create_task(conn, title="still needs handoff", parents=[owner], workspace_kind="dir")
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()


@pytest.mark.parametrize("evidence", ["legacy", "unknown", "malformed"])
def test_unbound_or_unknown_evidence_preserved(home, repo, evidence):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "scratch", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        payload = {"path": str(path), "reason": "shared" if evidence == "legacy" else "unknown"}
        kb._append_event(conn, owner, "workspace_cleanup_deferred_shared", payload)
        if evidence == "malformed":
            conn.execute("UPDATE task_events SET payload='[' WHERE task_id=? AND kind=?",
                         (owner, "workspace_cleanup_deferred_shared"))
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()


@pytest.mark.parametrize("change", ["directory", "kind", "path", "owner_live"])
def test_changed_owner_preserved(home, repo, change):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "scratch", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        if change == "directory":
            path.rename(path.with_name(path.name + "-original"))
            path.mkdir()
            (path / "replacement.txt").write_text("keep replacement", encoding="utf-8")
        elif change == "kind":
            conn.execute("UPDATE tasks SET workspace_kind='dir' WHERE id=?", (owner,))
        elif change == "path":
            conn.execute("UPDATE tasks SET workspace_path=? WHERE id=?", (str(path.parent), owner))
        else:
            conn.execute("UPDATE tasks SET status='running' WHERE id=?", (owner,))
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()


@pytest.mark.parametrize("change", ["dirty", "unpushed", "branch", "common"])
def test_changed_git_or_unique_work_preserved(home, repo, change):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "worktree", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        if change in {"dirty", "unpushed"}:
            (path / "needed.txt").write_text("needed work", encoding="utf-8")
            if change == "unpushed":
                _git("-C", str(path), "add", "needed.txt")
                _git("-C", str(path), "commit", "-m", "unpublished")
        elif change == "branch":
            _git("-C", str(path), "switch", "-c", "different-owner")
        else:
            other = repo.parent / "other-project"
            _git("clone", str(repo.parent / "origin.git"), str(other))
            other_wt = other / ".worktrees" / owner
            kbw._ensure_git_worktree(other, other_wt, f"wt/{owner}")
            (path / ".git").write_text((other_wt / ".git").read_text(), encoding="utf-8")
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()


def test_unreadable_sibling_preserves_all_candidates(home, repo):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "scratch", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        broken = kb.boards_root() / "unreadable" / "kanban.db"
        broken.parent.mkdir(parents=True)
        broken.write_bytes(b"not a SQLite database")
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()


def test_alias_borrower_and_same_id_cross_board(home, repo, monkeypatch):
    kb.create_board("consumer")
    with kbc.connect_closing() as owner_conn, kbc.connect_closing(board="consumer") as conn:
        owner, path = _owner(owner_conn, "scratch", repo)
        monkeypatch.setattr(kb, "_new_task_id", lambda: owner)
        alias = path / ".." / path.name
        borrower = _borrower(conn, alias)
        assert borrower == owner
        assert kb.archive_task(owner_conn, owner)
        assert path.is_dir()
        before = owner_conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
        assert kb.archive_task(conn, borrower)
        assert not path.exists()
        assert owner_conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == before


def test_final_identity_rechecked_after_lsp_release(home, repo, monkeypatch):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "worktree", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        monkeypatch.setattr(kbw, "release_lsp_clients", lambda _: _git("-C", str(path), "switch", "-c", "new-owner"))
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()
        assert _git("-C", str(path), "branch", "--show-current") == "new-owner"


def test_scratch_identity_rechecked_after_lsp_release(home, repo, monkeypatch):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "scratch", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)

        def replace_directory(_):
            path.rename(path.with_name(path.name + "-old"))
            path.mkdir()
            (path / "keep.txt").write_text("replacement", encoding="utf-8")

        monkeypatch.setattr(kbw, "release_lsp_clients", replace_directory)
        assert kb.archive_task(conn, borrower)
        assert (path / "keep.txt").read_text() == "replacement"


def test_worktree_retry_rechecks_identity(home, repo, monkeypatch):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "worktree", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        original_git, removals = kbw._git, []

        def failed_remove(repo_root, *args, timeout):
            if args[:2] == ("worktree", "remove"):
                removals.append(args)
                _git("-C", str(path), "switch", "-c", "changed-before-retry")
                return subprocess.CompletedProcess(args, 1, "", "synthetic removal failure")
            return original_git(repo_root, *args, timeout=timeout)

        monkeypatch.setattr(kbw, "_git", failed_remove)
        assert kb.archive_task(conn, borrower)
        assert len(removals) == 1
        assert path.is_dir()


def test_named_board_owner_released_without_owner_mutation(home, repo, monkeypatch):
    kb.create_board("owner")
    with kbc.connect_closing(board="owner") as owner_conn, kbc.connect_closing() as conn:
        owner, path = _owner(owner_conn, "worktree", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(owner_conn, owner)
        before = owner_conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
        tmux_calls = []
        monkeypatch.setattr(kbw, "_cleanup_worker_tmux", lambda *args: tmux_calls.append(args))
        assert kb.archive_task(conn, borrower)
        assert not path.exists()
        assert not tmux_calls
        assert owner_conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == before


def test_unmanaged_scratch_never_gets_continuation_identity(home, repo):
    with kbc.connect_closing() as conn:
        owner = kb.create_task(conn, title="non-owned scratch", workspace_kind="scratch", workspace_path=str(repo))
        borrower = _borrower(conn, repo)
        assert kbw._defer_shared_workspace_cleanup(conn, owner, repo)
        assert kb.archive_task(conn, owner)
        assert kb.archive_task(conn, borrower)
        assert (repo / "README.md").is_file()


@pytest.mark.parametrize("field", ["board", "task_id", "board_inode"])
def test_wrong_owner_namespace_preserved(home, repo, field):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "scratch", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        event = conn.execute("SELECT id, payload FROM task_events WHERE task_id=? AND kind=?",
                             (owner, "workspace_cleanup_deferred_shared")).fetchone()
        payload = json.loads(event["payload"])
        payload["identity"][field] = "different-owner"
        conn.execute("UPDATE task_events SET payload=? WHERE id=?", (json.dumps(payload), event["id"]))
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()


def test_replaced_git_admin_directory_preserved(home, repo):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "worktree", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        admin = kbw._git_abs_path(path, "--git-dir")
        previous = admin.with_name(admin.name + "-previous")
        admin.rename(previous)
        shutil.copytree(previous, admin)
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()


def test_replaced_owner_database_preserved(home, repo):
    kb.create_board("owner")
    with kbc.connect_closing(board="owner") as owner_conn, kbc.connect_closing() as conn:
        owner, path = _owner(owner_conn, "scratch", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(owner_conn, owner)
        database = Path(kbc._main_db_file(owner_conn))
    # All handles are closed before replacing this synthetic owner database.
    old = database.with_name("previous.db")
    database.rename(old)
    shutil.copy2(old, database)
    with kbc.connect_closing() as conn:
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()


def test_continuation_failure_does_not_block_terminal_transition(home, repo, monkeypatch):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "scratch", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)

        def unavailable_lsp(_):
            raise RuntimeError("synthetic lifecycle failure")

        monkeypatch.setattr(kbw, "release_lsp_clients", unavailable_lsp)
        assert kb.archive_task(conn, borrower)
        assert kb.get_task(conn, borrower).status == "archived"
        assert path.is_dir()


@pytest.mark.parametrize("owner_kind", ["scratch", "worktree"])
@pytest.mark.parametrize("borrower_kind", ["scratch", "worktree", "dir"])
@pytest.mark.parametrize("other_board", [False, True])
def test_pathless_children_hold_terminal_borrower_workspace(home, repo, owner_kind, borrower_kind, other_board):
    if other_board:
        kb.create_board("borrower")
    with kbc.connect_closing() as owner_conn, kbc.connect_closing(board="borrower" if other_board else None) as conn:
        owner, path = _owner(owner_conn, owner_kind, repo)
        borrower = kb.create_task(conn, title="borrower with handoff", workspace_kind=borrower_kind, workspace_path=str(path))
        first = kb.create_task(conn, title="first handoff consumer", parents=[borrower], workspace_kind="dir")
        last = kb.create_task(conn, title="last handoff consumer", parents=[borrower], workspace_kind="dir")
        assert kb.archive_task(owner_conn, owner)
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()
        assert kb.archive_task(conn, first)
        assert path.is_dir()
        assert kb.archive_task(conn, last)
        assert not path.exists()


def test_cross_board_id_collision_retains_pathless_children(home, repo, monkeypatch):
    kb.create_board("borrower")
    with kbc.connect_closing() as owner_conn, kbc.connect_closing(board="borrower") as conn:
        owner, path = _owner(owner_conn, "scratch", repo)
        with monkeypatch.context() as patch:
            patch.setattr(kb, "_new_task_id", lambda: owner)
            borrower = _borrower(conn, path)
        child = kb.create_task(conn, title="pathless handoff", parents=[borrower], workspace_kind="dir")
        assert borrower == owner
        assert kb.archive_task(owner_conn, owner)
        assert kb.archive_task(conn, borrower)
        assert path.is_dir()
        assert kb.archive_task(conn, child)
        assert not path.exists()


def test_new_handoff_consumer_rechecked_before_removal(home, repo, monkeypatch):
    with kbc.connect_closing() as conn:
        owner, path = _owner(conn, "scratch", repo)
        borrower = _borrower(conn, path)
        assert kb.archive_task(conn, owner)
        children = []

        def new_child(_):
            children.append(kb.create_task(conn, title="new handoff", parents=[borrower], workspace_kind="dir"))

        monkeypatch.setattr(kbw, "release_lsp_clients", new_child)
        assert kb.archive_task(conn, borrower)
        assert len(children) == 1
        assert path.is_dir()
