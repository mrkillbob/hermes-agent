from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from github_pr_feedback.controller import (
    LocalGitRepository,
    PooledLocalGitRepository,
    WorktreePoolExhausted,
    _prepare_receipt_worktree_with_overflow,
)
from github_pr_feedback.ledger import FeedbackLedger
from github_pr_feedback.policy import FeedbackReceipt


def initialized_repository(tmp_path: Path) -> Path:
    path = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "--quiet", "--allow-empty", "-m", "root"],
        check=True,
    )
    return path


def commit(path: Path, filename: str, content: str) -> str:
    (path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", filename], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "--quiet", "-m", filename],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def receipt(
    sha: str, *, pr_number: int = 17, repository: str = "acme/widgets"
) -> FeedbackReceipt:
    return FeedbackReceipt(repository, pr_number, "pr_local_ci", "local-ci-audit-v2", sha)


class MutableClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def make_governed_venv(repo: Path) -> None:
    venv = repo / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n", encoding="utf-8")


def commit_case_colliding_paths(repo: Path) -> str:
    """Create a commit whose tree has two paths that collide on macOS."""

    blobs: list[tuple[str, str]] = []
    for path, content in (
        ("agent@Agents-Mac-mini.local", "upper\n"),
        ("agent@agents-Mac-mini.local", "lower\n"),
    ):
        result = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
            input=content,
            check=True,
            capture_output=True,
            text=True,
        )
        blobs.append((path, result.stdout.strip()))
    emails_tree = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        input="".join(f"100644 blob {sha}\t{path}\n" for path, sha in blobs),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    contributors_tree = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        input=f"040000 tree {emails_tree}\temails\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    root_tree = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        input=f"040000 tree {contributors_tree}\tcontributors\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    result = subprocess.run(
        ["git", "-C", str(repo), "commit-tree", root_tree],
        input="case-collision\n",
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def test_pool_reuses_the_same_slot_directory_after_release(tmp_path: Path) -> None:
    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    prepared_a = pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    assert prepared_a.expected_sha == sha_a
    assert (prepared_a.path / "a.txt").is_file()

    from github_pr_feedback.ledger import WorktreeSlotLease

    lease_row = ledger._connection.execute(
        "SELECT slot_id, lease_version, owner_pid FROM worktree_pool_slots"
    ).fetchone()
    pool.release(WorktreeSlotLease(*lease_row))

    prepared_b = pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    assert prepared_b.expected_sha == sha_b
    assert prepared_b.path == prepared_a.path  # same physical slot-0 directory reused
    assert (prepared_b.path / "a.txt").is_file()  # still tracked at the new head
    assert (prepared_b.path / "b.txt").is_file()
    ledger.close()


def test_pool_uses_distinct_slot_directories_for_distinct_repositories(
    tmp_path: Path,
) -> None:
    repo_a = initialized_repository(tmp_path / "repo-a")
    sha_a = commit(repo_a, "a.txt", "a")
    repo_b = initialized_repository(tmp_path / "repo-b")
    sha_b = commit(repo_b, "other.txt", "other")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    first = pool.prepare_receipt_worktree(repo_a, receipt(sha_a, pr_number=1))
    lease_row = ledger._connection.execute(
        "SELECT slot_id, lease_version, owner_pid FROM worktree_pool_slots"
    ).fetchone()
    from github_pr_feedback.ledger import WorktreeSlotLease

    pool.release(WorktreeSlotLease(*lease_row))
    second = pool.prepare_receipt_worktree(
        repo_b, receipt(sha_b, pr_number=2, repository="acme/other")
    )

    assert second.path != first.path
    assert first.path.is_dir()
    assert (first.path / "a.txt").is_file()
    assert (second.path / "other.txt").is_file()
    ledger.close()


def test_pool_preserves_legacy_global_slot_directory(tmp_path: Path) -> None:
    repo = initialized_repository(tmp_path / "repo")
    sha = commit(repo, "a.txt", "a")
    pool_root = tmp_path / "pool"
    legacy_slot = pool_root / "slot-0"
    legacy_slot.mkdir(parents=True)
    legacy_marker = legacy_slot / "owned-wip.txt"
    legacy_marker.write_text("preserve me", encoding="utf-8")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, pool_root, slot_count=1, owner_pid=lambda: 4242
    )

    prepared = pool.prepare_receipt_worktree(repo, receipt(sha))

    assert prepared.path != legacy_slot
    assert legacy_marker.read_text(encoding="utf-8") == "preserve me"
    ledger.close()


def test_pool_refuses_when_every_slot_is_leased_and_not_stale(tmp_path: Path) -> None:
    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger,
        tmp_path / "pool",
        slot_count=1,
        owner_pid=lambda: 4242,
        pid_is_alive=lambda _pid: True,
    )

    pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    with pytest.raises(WorktreePoolExhausted):
        pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    ledger.close()


def test_pool_reclaims_a_stale_lease_without_an_explicit_release(tmp_path: Path) -> None:
    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=UTC))
    pool = PooledLocalGitRepository(
        ledger,
        tmp_path / "pool",
        slot_count=1,
        owner_pid=lambda: 4242,
        pid_is_alive=lambda _pid: True,
        clock=clock,
        lease_timeout=timedelta(hours=1),
    )

    pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    # Still within the lease: a second acquire attempt must refuse.
    clock.now += timedelta(minutes=30)
    with pytest.raises(WorktreePoolExhausted):
        pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))

    # Past the lease timeout: reclaimable even though nothing ever called release().
    clock.now += timedelta(hours=1)
    prepared = pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    assert prepared.expected_sha == sha_b
    ledger.close()


def test_pool_reclaims_an_unbound_lease_when_its_owner_process_is_dead(tmp_path: Path) -> None:
    """A crashed dispatcher cannot finish binding its slot to a Kanban task."""

    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger,
        tmp_path / "pool",
        slot_count=1,
        owner_pid=lambda: 4242,
        pid_is_alive=lambda _pid: False,
    )

    pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    # The lease is fresh, but the owning dispatcher is gone before bind_task().
    prepared = pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))

    assert prepared.expected_sha == sha_b
    ledger.close()


def test_pool_release_makes_a_slot_immediately_reusable(tmp_path: Path) -> None:
    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    prepared_a = pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    lease = ledger._connection.execute(
        "SELECT slot_id, lease_version, owner_pid FROM worktree_pool_slots"
    ).fetchone()
    from github_pr_feedback.ledger import WorktreeSlotLease

    pool.release(WorktreeSlotLease(lease[0], lease[1], lease[2]))

    prepared_b = pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    assert prepared_b.path == prepared_a.path
    assert prepared_b.expected_sha == sha_b
    ledger.close()


def test_pool_never_removes_the_linked_venv_between_reuses(tmp_path: Path) -> None:
    repo = initialized_repository(tmp_path)
    make_governed_venv(repo)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    prepared_a = pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    venv_link = prepared_a.path / ".venv"
    assert venv_link.is_symlink()
    assert (venv_link / "bin" / "python").is_file()
    # Simulate build/test byproducts a real CI run would leave behind.
    (prepared_a.path / "__pycache__").mkdir()
    (prepared_a.path / "__pycache__" / "junk.pyc").write_text("x", encoding="utf-8")
    (prepared_a.path / "stray_untracked.txt").write_text("x", encoding="utf-8")

    lease = ledger._connection.execute(
        "SELECT slot_id, lease_version, owner_pid FROM worktree_pool_slots"
    ).fetchone()
    from github_pr_feedback.ledger import WorktreeSlotLease

    pool.release(WorktreeSlotLease(lease[0], lease[1], lease[2]))
    prepared_b = pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))

    assert venv_link.is_symlink()
    assert (venv_link / "bin" / "python").is_file()
    assert not (prepared_b.path / "__pycache__").exists()
    assert not (prepared_b.path / "stray_untracked.txt").exists()
    ledger.close()


def test_link_governed_venv_accepts_profile_managed_environment(tmp_path: Path) -> None:
    profile_root = tmp_path / ".hermes"
    repo = initialized_repository(profile_root)
    managed_venv = profile_root / "venvs" / "hermes-3136"
    (managed_venv / "bin").mkdir(parents=True)
    (managed_venv / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / ".venv").symlink_to(managed_venv, target_is_directory=True)
    workspace = tmp_path / "receipt-worktree"
    workspace.mkdir()

    LocalGitRepository._link_governed_venv(repo, workspace)

    assert (workspace / ".venv").is_symlink()
    assert (workspace / ".venv").resolve(strict=True) == managed_venv.resolve(strict=True)


class FakeKanban:
    def __init__(self, statuses: dict[tuple[str, str], str | None]) -> None:
        self._statuses = statuses
        self.calls: list[tuple[str, str]] = []

    def task_status(self, board: str, task_id: str) -> str | None:
        self.calls.append((board, task_id))
        return self._statuses.get((board, task_id))


def test_reconcile_releases_a_slot_whose_task_is_done(tmp_path: Path) -> None:
    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    prepared_a = pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    pool.bind_task(receipt(sha_a, pr_number=1), "task-1", "repairs")

    kanban = FakeKanban({("repairs", "task-1"): "done"})
    released = pool.reconcile_leases(kanban)
    assert released == 1

    prepared_b = pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    assert prepared_b.path == prepared_a.path  # reclaimed without waiting for the lease timeout
    ledger.close()


@pytest.mark.parametrize("status", ["blocked", "triage"])
def test_reconcile_keeps_a_slot_for_a_retryable_task(
    tmp_path: Path, status: str
) -> None:
    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    prepared_a = pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    pool.bind_task(receipt(sha_a, pr_number=1), "task-1", "repairs")

    released = pool.reconcile_leases(
        FakeKanban({("repairs", "task-1"): status})
    )
    assert released == 0

    with pytest.raises(WorktreePoolExhausted):
        pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    assert prepared_a.path.is_dir()
    ledger.close()


def test_reconcile_keeps_bound_retryable_task_past_stale_timeout(
    tmp_path: Path,
) -> None:
    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    now = [datetime(2026, 8, 31, tzinfo=UTC)]
    pool = PooledLocalGitRepository(
        ledger,
        tmp_path / "pool",
        slot_count=1,
        owner_pid=lambda: 4242,
        clock=lambda: now[0],
        lease_timeout=timedelta(hours=10),
    )

    pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    pool.bind_task(receipt(sha_a, pr_number=1), "task-1", "repairs")
    now[0] += timedelta(hours=11)

    assert pool.reconcile_leases(FakeKanban({("repairs", "task-1"): "blocked"})) == 0
    with pytest.raises(WorktreePoolExhausted):
        pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    ledger.close()


def test_reconcile_leaves_a_slot_whose_task_is_still_running(tmp_path: Path) -> None:
    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    pool.bind_task(receipt(sha_a, pr_number=1), "task-1", "repairs")

    kanban = FakeKanban({("repairs", "task-1"): "running"})
    released = pool.reconcile_leases(kanban)
    assert released == 0

    with pytest.raises(WorktreePoolExhausted):
        pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    ledger.close()


def test_reconcile_releases_a_slot_whose_task_has_vanished(tmp_path: Path) -> None:
    """A board that no longer knows the task is not positive evidence anything

    is still using the slot -- same "missing is not active" interpretation
    the codebase already uses for base-refresh task bindings.
    """

    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    prepared_a = pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    pool.bind_task(receipt(sha_a, pr_number=1), "task-1", "repairs")

    kanban = FakeKanban({})  # task_status returns None: unknown to the board
    released = pool.reconcile_leases(kanban)
    assert released == 1

    prepared_b = pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    assert prepared_b.path == prepared_a.path
    ledger.close()


def test_reconcile_ignores_a_slot_with_no_bound_task_yet(tmp_path: Path) -> None:
    """A slot mid-dispatch (acquired but bind_task not yet called) has nothing

    for reconcile to check and must be left alone while its dispatcher is
    live -- it stays protected by its lease timeout, never silently reclaimed.
    """

    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger,
        tmp_path / "pool",
        slot_count=1,
        owner_pid=lambda: 4242,
        pid_is_alive=lambda _pid: True,
    )

    pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    # No bind_task call.

    released = pool.reconcile_leases(FakeKanban({}))
    assert released == 0
    with pytest.raises(WorktreePoolExhausted):
        pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    ledger.close()


def test_pool_does_not_reclaim_a_dead_owner_lease_that_started_dispatching(
    tmp_path: Path,
) -> None:
    """mark_task_dispatching() runs before create_or_get_task(); if the

    dispatcher dies afterward but before bind_task() runs, create_or_get_task()
    may have already idempotently succeeded and a worker may already be using
    the workspace. The dead-PID fast path must not treat that lease as
    conclusively abandoned just because task_id is still unset -- it must be
    left to the full lease timeout, same as a lease that did finish binding.
    """

    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger,
        tmp_path / "pool",
        slot_count=1,
        owner_pid=lambda: 4242,
        pid_is_alive=lambda _pid: False,
    )

    pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    # Task creation was attempted but never confirmed bound before the
    # dispatcher died.
    pool.mark_task_dispatching(receipt(sha_a, pr_number=1), "dispatch-key-1")

    with pytest.raises(WorktreePoolExhausted):
        pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))
    ledger.close()


def test_pool_reclaims_dead_owner_lease_once_dispatching_completes_and_binds(
    tmp_path: Path,
) -> None:
    """Once bind_task() actually lands, the lease is governed by task

    reconciliation/timeout as before -- mark_task_dispatching() alone must
    not create a permanently stuck slot.
    """

    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger,
        tmp_path / "pool",
        slot_count=1,
        owner_pid=lambda: 4242,
        pid_is_alive=lambda _pid: False,
    )

    pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    pool.mark_task_dispatching(receipt(sha_a, pr_number=1), "dispatch-key-1")
    pool.bind_task(receipt(sha_a, pr_number=1), "task-1", "repairs")

    kanban = FakeKanban({("repairs", "task-1"): "done"})
    released = pool.reconcile_leases(kanban)
    assert released == 1
    ledger.close()


def test_reclaimed_slot_clears_prior_task_binding_for_the_new_lease(
    tmp_path: Path,
) -> None:
    """A slot re-leased after its previous occupant's task went terminal must

    not carry that occupant's task_id/board forward: otherwise a *new*
    dispatcher that also dies before binding its own task is invisible to
    the `task_id IS NULL` dead-unbound reclaim path, stranding the slot for
    the full lease timeout even though it is really unbound.

    A bound slot is never staleness-reclaimed by claim_worktree_slot in this
    pool -- only reconcile_leases releases it once its Kanban task goes
    terminal (see claim_worktree_slot's retryable-card protection). That
    release path (finish_worktree_slot) only flips status back to 'free'; it
    does not itself clear task_id/board, which is exactly the gap
    claim_worktree_slot's clearing closes on the next claim.
    """

    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    sha_b = commit(repo, "b.txt", "b")
    sha_c = commit(repo, "c.txt", "c")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=UTC))
    pool = PooledLocalGitRepository(
        ledger,
        tmp_path / "pool",
        slot_count=1,
        owner_pid=lambda: 4242,
        pid_is_alive=lambda _pid: True,
        clock=clock,
        lease_timeout=timedelta(hours=1),
    )

    # First occupant fully binds a task, then that task goes terminal and
    # reconcile_leases releases the slot back to 'free' without clearing
    # task_id/board.
    pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=1))
    pool.bind_task(receipt(sha_a, pr_number=1), "task-1", "repairs")
    row = ledger._connection.execute(
        "SELECT task_id, board FROM worktree_pool_slots WHERE slot_id = 0"
    ).fetchone()
    assert row == ("task-1", "repairs")

    released = pool.reconcile_leases(FakeKanban({("repairs", "task-1"): "done"}))
    assert released == 1
    row = ledger._connection.execute(
        "SELECT status, task_id, board FROM worktree_pool_slots WHERE slot_id = 0"
    ).fetchone()
    assert row == ("free", "task-1", "repairs")

    clock.now += timedelta(minutes=1)
    pool.prepare_receipt_worktree(repo, receipt(sha_b, pr_number=2))

    # The re-leased slot must not carry the previous occupant's binding.
    row = ledger._connection.execute(
        "SELECT task_id, board FROM worktree_pool_slots WHERE slot_id = 0"
    ).fetchone()
    assert row == (None, None)

    # And, being genuinely unbound, it is now visible to the dead-PID fast
    # reclaim path if its new owner also dies before binding -- it must not
    # be forced to wait out the full lease timeout again.
    pool_dead = PooledLocalGitRepository(
        ledger,
        tmp_path / "pool",
        slot_count=1,
        owner_pid=lambda: 4343,
        pid_is_alive=lambda _pid: False,
        clock=clock,
        lease_timeout=timedelta(hours=1),
    )
    clock.now += timedelta(minutes=1)
    prepared_c = pool_dead.prepare_receipt_worktree(repo, receipt(sha_c, pr_number=3))
    assert prepared_c.expected_sha == sha_c
    ledger.close()


def test_pool_prepare_failure_releases_the_slot_immediately(tmp_path: Path) -> None:
    repo = initialized_repository(tmp_path)
    sha_a = commit(repo, "a.txt", "a")
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    bogus = receipt("f" * 40, pr_number=1)  # well-formed SHA that doesn't exist anywhere
    with pytest.raises(Exception):
        pool.prepare_receipt_worktree(repo, bogus)

    # The failed acquire must not strand the only slot -- immediately usable.
    prepared = pool.prepare_receipt_worktree(repo, receipt(sha_a, pr_number=2))
    assert prepared.expected_sha == sha_a
    ledger.close()


def test_receipt_preparation_uses_exact_head_overflow_when_pool_is_exhausted(
    tmp_path: Path,
) -> None:
    repo = initialized_repository(tmp_path)
    sha = commit(repo, "a.txt", "a")

    class ExhaustedPool:
        def prepare_receipt_worktree(self, _path: Path, _receipt: FeedbackReceipt):
            raise WorktreePoolExhausted("pool full")

    prepared = _prepare_receipt_worktree_with_overflow(
        ExhaustedPool(), repo, receipt(sha), tmp_path / "overflow-worktrees"
    )

    assert prepared.expected_sha == sha
    assert prepared.path.is_relative_to(tmp_path / "overflow-worktrees")


def test_pool_excludes_case_colliding_tracked_paths_on_case_insensitive_fs(
    tmp_path: Path,
) -> None:
    repo = initialized_repository(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.ignorecase", "true"],
        check=True,
    )
    sha = commit_case_colliding_paths(repo)
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    pool = PooledLocalGitRepository(
        ledger, tmp_path / "pool", slot_count=1, owner_pid=lambda: 4242
    )

    prepared = pool.prepare_receipt_worktree(repo, receipt(sha))

    result = subprocess.run(
        [
            "git",
            "-C",
            str(prepared.path),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            ".",
            ":!.venv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == ""
    ledger.close()
