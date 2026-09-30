"""Persistent capacity and allocation-boundary regressions using isolated filesystems."""

import multiprocessing
import os
import sqlite3
import subprocess
import tempfile
import unittest

import pytest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from github_pr_feedback.worktree_capacity import (
    WorktreeCapacityAdmission,
    WorktreeCapacityPolicy,
    WorktreeCapacityRejected,
)


def probe_20(_):
    return SimpleNamespace(free=20)


def competing_allocator(state, path, barrier, results):
    try:
        admission = WorktreeCapacityAdmission(
            Path(state), WorktreeCapacityPolicy(2, 8), disk_usage=probe_20
        )
        barrier.wait(timeout=10)
        handle = admission.reserve(Path(path))
        # Leave reservations allocating, as a live actor would; no automatic release.
        results.put((True, handle.path))
    except WorktreeCapacityRejected:
        results.put((False, path))


class WorktreeCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "worktree-capacity.sqlite3"
        self.policy = WorktreeCapacityPolicy(2, 8)
        self.admission = WorktreeCapacityAdmission(
            self.state, self.policy, disk_usage=probe_20
        )

    def tearDown(self):
        self.temp.cleanup()

    def rows(self):
        with closing(sqlite3.connect(self.state)) as db:
            return db.execute(
                "SELECT path,token,requested_bytes,phase FROM reservations ORDER BY path"
            ).fetchall()

    def test_pressure_rejects_before_workspace_creation_and_rolls_back(self):
        self.admission.reserve(self.root / "a")
        self.admission.reserve(self.root / "b")
        with self.assertRaisesRegex(WorktreeCapacityRejected, "insufficient space"):
            self.admission.reserve(self.root / "c" / "workspace")
        self.assertFalse((self.root / "c").exists())
        self.assertEqual(len(self.rows()), 2)

    def test_same_path_retained_reuse_does_not_double_charge_and_refences(self):
        path = self.root / "a"
        with self.admission.reserve(path) as first:
            path.mkdir()
        with self.admission.reserve(path) as second:
            self.assertNotEqual(first.token, second.token)
            self.assertEqual(len(self.rows()), 1)
            with self.assertRaisesRegex(
                WorktreeCapacityRejected, "stale reservation fence"
            ):
                self.admission._finish(first, successful=True)
        self.assertEqual(self.rows()[0][2:], (8, "retained"))

    def test_same_path_competing_operation_is_rejected(self):
        self.admission.reserve(self.root / "a")
        other = WorktreeCapacityAdmission(self.state, self.policy, disk_usage=probe_20)
        with self.assertRaisesRegex(WorktreeCapacityRejected, "allocating"):
            other.reserve(self.root / "a")
        self.assertEqual(len(self.rows()), 1)

    def test_independent_path_admission_not_locked_by_long_operation(self):
        with self.admission.reserve(self.root / "a"):
            other = WorktreeCapacityAdmission(
                self.state, self.policy, disk_usage=probe_20
            )
            with other.reserve(self.root / "b"):
                (self.root / "b").mkdir()
                (self.root / "a").mkdir()
        self.assertEqual([r[3] for r in self.rows()], ["retained", "retained"])

    def test_uncertain_partial_failure_retains_quota_and_preserves_work(self):
        path = self.root / "a"
        with (
            self.assertRaisesRegex(RuntimeError, "allocation failed"),
            self.admission.reserve(path),
        ):
            path.mkdir()
            (path / "partial.bin").write_bytes(b"owned\x00binary")
            raise RuntimeError("allocation failed")
        self.assertEqual((path / "partial.bin").read_bytes(), b"owned\x00binary")
        self.assertEqual(self.rows()[0][3], "uncertain")
        with self.assertRaises(WorktreeCapacityRejected):
            self.admission.reserve(path)
        self.admission.reserve(self.root / "b")
        with self.assertRaises(WorktreeCapacityRejected):
            self.admission.reserve(self.root / "c")

    def test_unknown_existing_workspace_is_not_adopted(self):
        path = self.root / "legacy"
        path.mkdir()
        with self.assertRaisesRegex(WorktreeCapacityRejected, "legacy"):
            self.admission.reserve(path)
        self.assertEqual(self.rows(), [])

    def test_probe_error_rolls_back_without_exposing_error_payload(self):
        def broken(_):
            raise OSError("private arbitrary payload")

        admission = WorktreeCapacityAdmission(
            self.state, self.policy, disk_usage=broken
        )
        with self.assertRaises(WorktreeCapacityRejected) as error:
            admission.reserve(self.root / "a")
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(self.rows(), [])

    def test_actual_symlink_ancestor_is_protected(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        if os.name == "nt":
            from tools.environments.local import build_subprocess_env

            subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
                env=build_subprocess_env(inherit_profile_home=False),
            )
        else:
            link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(WorktreeCapacityRejected, "symlink or reparse"):
            self.admission.reserve(link / "workspace")
        self.assertFalse((target / "workspace").exists())
        self.assertEqual(self.rows(), [])

    def test_success_without_workspace_preserves_uncertainty_and_reports_error(self):
        with (
            self.assertRaisesRegex(WorktreeCapacityRejected, "lacks valid physical"),
            self.admission.reserve(self.root / "a"),
        ):
            pass
        self.assertEqual(self.rows()[0][3], "uncertain")

    def test_unknown_registry_does_not_get_reinitialized(self):
        state = self.root / "unknown.sqlite3"
        with closing(sqlite3.connect(state)) as db:
            db.execute("CREATE TABLE unique_evidence(value TEXT)")
        with self.assertRaisesRegex(WorktreeCapacityRejected, "unknown registry"):
            WorktreeCapacityAdmission(state, self.policy)
        with closing(sqlite3.connect(state)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall(),
                [("unique_evidence",)],
            )

    def test_real_processes_across_callers_cannot_overbook(self):
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(5)
        results = ctx.Queue()
        processes = [
            ctx.Process(
                target=competing_allocator,
                args=(
                    str(self.state),
                    str(self.root / f"caller-{i}"),
                    barrier,
                    results,
                ),
            )
            for i in range(5)
        ]
        try:
            for process in processes:
                process.start()
            values = [results.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sum(allowed for allowed, _ in values), 2)
            self.assertEqual(sum(r[2] for r in self.rows()), 16)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join()
            results.close()
            results.join_thread()

    def test_real_processes_same_path_have_one_allocation_fence(self):
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        results = ctx.Queue()
        processes = [
            ctx.Process(
                target=competing_allocator,
                args=(str(self.state), str(self.root / "shared"), barrier, results),
            )
            for _ in range(2)
        ]
        try:
            for process in processes:
                process.start()
            values = [results.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sum(allowed for allowed, _ in values), 1)
            self.assertEqual(len(self.rows()), 1)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join()
            results.close()
            results.join_thread()

    def test_real_processes_case_unicode_alias_share_single_fence(self):
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        results = ctx.Queue()
        paths = [self.root / "slot-Caf\u00e9", self.root / "SLOT-CAFE\u0301"]
        processes = [
            ctx.Process(
                target=competing_allocator,
                args=(str(self.state), str(path), barrier, results),
            )
            for path in paths
        ]
        try:
            for process in processes:
                process.start()
            values = [results.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sum(allowed for allowed, _ in values), 1)
            self.assertEqual(len(self.rows()), 1)
            self.assertEqual(self.rows()[0][2], 8)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join()
            results.close()
            results.join_thread()

    def test_policy_change_cannot_silently_reprice_existing_reservation(self):
        path = self.root / "a"
        with self.admission.reserve(path):
            path.mkdir()
        other = WorktreeCapacityAdmission(
            self.state, WorktreeCapacityPolicy(2, 4), disk_usage=probe_20
        )
        with self.assertRaisesRegex(WorktreeCapacityRejected, "policy mismatch"):
            other.reserve(path)
        self.assertEqual(self.rows()[0][2], 8)

    def test_existing_path_reuse_rechecks_floor_without_double_charging(self):
        path = self.root / "a"
        with self.admission.reserve(path):
            path.mkdir()
        exact = WorktreeCapacityAdmission(
            self.state, self.policy, disk_usage=lambda _: SimpleNamespace(free=10)
        )
        with exact.reserve(path):
            pass
        low = WorktreeCapacityAdmission(
            self.state, self.policy, disk_usage=lambda _: SimpleNamespace(free=9)
        )
        with self.assertRaisesRegex(WorktreeCapacityRejected, "insufficient space"):
            low.reserve(path)
        self.assertEqual(self.rows()[0][2:], (8, "retained"))

    def test_actual_filesystem_probe_and_workspace_completion(self):
        actual = WorktreeCapacityAdmission(self.state, WorktreeCapacityPolicy(0, 1))
        workspace = self.root / "actual-filesystem"
        with actual.reserve(workspace):
            workspace.mkdir()
            (workspace / "proof.bin").write_bytes(b"fixture")
        self.assertEqual(self.rows()[0][2:], (1, "retained"))

    def test_other_device_budget_is_separate(self):
        with closing(sqlite3.connect(self.state)) as db:
            db.execute(
                "INSERT INTO reservations VALUES (?,?,?,?,?)",
                ("/unavailable-other-device", "other", "protected", 999, "uncertain"),
            )
            db.commit()
        self.admission.reserve(self.root / "a")
        self.assertEqual(len(self.rows()), 2)

    def test_invalid_policy_and_invalid_probe_fail_closed(self):
        for value in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                WorktreeCapacityPolicy(value, 8)
        admission = WorktreeCapacityAdmission(
            self.state, self.policy, disk_usage=lambda _: SimpleNamespace(free=-1)
        )
        with self.assertRaisesRegex(WorktreeCapacityRejected, "probe invalid"):
            admission.reserve(self.root / "a")
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()


class NoGitRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv):
        self.calls.append(argv)
        raise AssertionError("Git must not run before admission")


def test_capacity_rejection_precedes_all_direct_and_maintenance_git(tmp_path):
    from github_pr_feedback.controller import LocalGitRepository
    from github_pr_feedback.policy import FeedbackReceipt
    import pytest

    root = tmp_path.resolve()
    capacity = WorktreeCapacityAdmission(
        root / "capacity.sqlite3",
        WorktreeCapacityPolicy(2, 8),
        disk_usage=lambda _: SimpleNamespace(free=9),
    )
    runner = NoGitRunner()
    workspaces = root / "workspaces"
    git = LocalGitRepository(workspaces, runner, capacity=capacity)
    receipt = FeedbackReceipt("acme/widgets", 1, "pr_local_ci", "test", "a" * 40)
    with pytest.raises(WorktreeCapacityRejected, match="insufficient space"):
        git.prepare_receipt_worktree(root / "repository", receipt)
    with pytest.raises(WorktreeCapacityRejected, match="insufficient space"):
        git.prepare_maintenance_worktree(
            root / "repository", "acme/widgets", "a" * 40, "tests"
        )
    assert runner.calls == []
    assert not workspaces.exists()


def test_pooled_capacity_rejection_cannot_escape_into_overflow(tmp_path):
    from github_pr_feedback.controller import (
        PooledLocalGitRepository,
        _prepare_receipt_worktree_with_overflow,
    )
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.policy import FeedbackReceipt
    import pytest

    root = tmp_path.resolve()
    capacity = WorktreeCapacityAdmission(
        root / "capacity.sqlite3",
        WorktreeCapacityPolicy(2, 8),
        disk_usage=lambda _: SimpleNamespace(free=9),
    )
    ledger = FeedbackLedger(root / "ledger.sqlite3")
    try:
        runner = NoGitRunner()
        pool = PooledLocalGitRepository(
            ledger, root / "pool", runner, capacity=capacity
        )
        receipt = FeedbackReceipt("acme/widgets", 1, "pr_local_ci", "test", "a" * 40)
        with pytest.raises(WorktreeCapacityRejected, match="insufficient space"):
            _prepare_receipt_worktree_with_overflow(
                pool, root / "repository", receipt, root / "overflow"
            )
        assert runner.calls == []
        assert not (root / "pool").exists()
        assert not (root / "overflow").exists()
        assert not ledger.leased_worktree_slots()
    finally:
        ledger.close()


def test_full_pool_overflow_shares_admission_and_rejects_before_git(tmp_path):
    from github_pr_feedback.controller import (
        WorktreePoolExhausted,
        _prepare_receipt_worktree_with_overflow,
    )
    from github_pr_feedback.policy import FeedbackReceipt
    import pytest

    root = tmp_path.resolve()
    capacity = WorktreeCapacityAdmission(
        root / "capacity.sqlite3",
        WorktreeCapacityPolicy(2, 8),
        disk_usage=lambda _: SimpleNamespace(free=9),
    )

    class FullPool:
        _capacity = capacity

        def prepare_receipt_worktree(self, path, receipt):
            raise WorktreePoolExhausted("leased")

    receipt = FeedbackReceipt("acme/widgets", 1, "pr_local_ci", "test", "a" * 40)
    with pytest.raises(WorktreeCapacityRejected, match="insufficient space"):
        _prepare_receipt_worktree_with_overflow(
            FullPool(), root / "missing", receipt, root / "overflow"
        )
    assert not (root / "overflow").exists()


def test_control_registry_shared_across_profile_ledgers(tmp_path, monkeypatch):
    import hermes_constants
    from github_pr_feedback.worktree_capacity import control_capacity_admission
    import pytest

    root = tmp_path.resolve()
    monkeypatch.setattr(hermes_constants, "get_default_hermes_root", lambda: root)
    first = control_capacity_admission(policy=WorktreeCapacityPolicy(2, 8))
    second = control_capacity_admission(policy=WorktreeCapacityPolicy(2, 8))
    assert (
        first.state_path
        == second.state_path
        == root / "state" / "worktree-capacity.sqlite3"
    )
    first.reserve(root / "new")
    with pytest.raises(WorktreeCapacityRejected, match="allocating"):
        second.reserve(root / "new")


def test_case_and_unicode_aliases_cannot_hold_competing_allocations(tmp_path):
    import pytest

    root = tmp_path.resolve()
    admission = WorktreeCapacityAdmission(
        root / "capacity.sqlite3",
        WorktreeCapacityPolicy(2, 8),
        disk_usage=probe_20,
    )
    admission.reserve(root / "slot-Caf\u00e9")
    with pytest.raises(WorktreeCapacityRejected, match="path alias"):
        admission.reserve(root / "SLOT-CAFE\u0301")
    with closing(sqlite3.connect(admission.state_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 1


def test_review_capacity_rejection_never_dispatches_from_main_clone(tmp_path):
    from github_pr_feedback.cli import _review_required_task
    import pytest

    class RejectedWorkspaces:
        def prepare_maintenance_worktree(self, *args):
            raise WorktreeCapacityRejected("storage hold")

    merge_policy = SimpleNamespace(repository="acme/widgets")
    policy = SimpleNamespace(
        targets={"acme/widgets": SimpleNamespace(local_path=tmp_path)}
    )
    pull_request = SimpleNamespace(number=17, head_sha="a" * 40)
    with pytest.raises(WorktreeCapacityRejected, match="storage hold"):
        _review_required_task(policy, merge_policy, pull_request, RejectedWorkspaces())


@pytest.fixture
def fresh_pool(tmp_path, monkeypatch):
    from github_pr_feedback.controller import PooledLocalGitRepository, _pool_source_namespace
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.policy import FeedbackReceipt
    source = tmp_path / 'configured-source'
    source.mkdir()
    capacity = WorktreeCapacityAdmission(tmp_path / 'registry.sqlite3',
                    WorktreeCapacityPolicy(2, 8), disk_usage=probe_20)
    ledger = FeedbackLedger(tmp_path / 'ledger.sqlite3')
    pool = PooledLocalGitRepository(ledger, tmp_path / 'pool', slot_count=2, capacity=capacity)
    receipt = FeedbackReceipt('example/project', 1, 'pr_local_ci', 'test', 'a' * 40)
    namespace = _pool_source_namespace(source)
    calls = []
    monkeypatch.setattr(pool, '_ensure_exact_head', lambda *args: calls.append(('fetch', args)))
    def prepare(path, slot_id, receipt, *, namespace):
        candidate = pool._slot_path(receipt, slot_id, namespace)
        calls.append(('prepare', slot_id))
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    monkeypatch.setattr(pool, '_prepare_slot', prepare)
    yield source, pool, receipt, namespace, capacity, ledger, calls
    ledger.close()


def test_unknown_first_slot_preserved_and_later_fresh_slot_admitted(fresh_pool):
    from github_pr_feedback.controller import _pool_ledger_slot
    source, pool, receipt, namespace, capacity, ledger, calls = fresh_pool
    unknown = pool._slot_path(receipt, 0, namespace)
    unknown.mkdir(parents=True)
    marker = unknown / 'owned.txt'
    marker.write_text('preserved', encoding='utf-8')
    prepared = pool.prepare_receipt_worktree(source, receipt)
    assert prepared.path == pool._slot_path(receipt, 1, namespace)
    assert marker.read_text(encoding='utf-8-sig') == 'preserved'
    assert calls[-1] == ('prepare', 1)
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT 1 FROM worktree_pool_slots WHERE slot_id=?",
                          (_pool_ledger_slot(namespace, 0),)).fetchone() is None
    with sqlite3.connect(capacity.state_path) as db:
        assert db.execute('SELECT path,requested_bytes,phase FROM reservations').fetchall() == [
            (str(prepared.path), 8, 'retained')]


def test_all_unknown_slots_preserved_without_lease_or_git(fresh_pool):
    from github_pr_feedback.controller import WorktreePoolExhausted
    source, pool, receipt, namespace, capacity, ledger, calls = fresh_pool
    for slot in range(2):
        pool._slot_path(receipt, slot, namespace).mkdir(parents=True)
    with pytest.raises(WorktreePoolExhausted):
        pool.prepare_receipt_worktree(source, receipt)
    assert calls == []
    assert ledger.leased_worktree_slots() == ()
    with sqlite3.connect(capacity.state_path) as db:
        assert db.execute('SELECT * FROM reservations').fetchall() == []


def test_candidate_created_after_selection_is_rejected_before_git(fresh_pool, monkeypatch):
    source, pool, receipt, namespace, capacity, ledger, calls = fresh_pool
    original = capacity.is_unregistered_existing
    def concurrent_creation(candidate):
        assert not original(candidate)
        candidate.mkdir(parents=True)
        (candidate / 'owned.txt').write_text('concurrent owner', encoding='utf-8')
        return False
    monkeypatch.setattr(capacity, 'is_unregistered_existing', concurrent_creation)
    with pytest.raises(WorktreeCapacityRejected, match='legacy'):
        pool.prepare_receipt_worktree(source, receipt)
    assert calls == []
    assert not ledger.leased_worktree_slots()


def test_unknown_selection_does_not_mask_insufficient_space(fresh_pool):
    source, pool, receipt, namespace, capacity, ledger, calls = fresh_pool
    pool._slot_path(receipt, 0, namespace).mkdir(parents=True)
    capacity._probe = lambda _: SimpleNamespace(free=9)
    with pytest.raises(WorktreeCapacityRejected, match='insufficient space'):
        pool.prepare_receipt_worktree(source, receipt)
    assert calls == []
    assert not ledger.leased_worktree_slots()


def test_registered_uncertain_first_slot_is_hard_gate(fresh_pool):
    source, pool, receipt, namespace, capacity, ledger, calls = fresh_pool
    candidate = pool._slot_path(receipt, 0, namespace)
    handle = capacity.reserve(candidate)
    candidate.mkdir(parents=True)
    capacity._finish(handle, successful=False)
    assert not capacity.is_unregistered_existing(candidate)
    with pytest.raises(WorktreeCapacityRejected, match='uncertain'):
        pool.prepare_receipt_worktree(source, receipt)
    assert calls == []
    assert not pool._slot_path(receipt, 1, namespace).exists()


def test_selection_alias_and_unknown_registry_remain_hard_gates(tmp_path):
    capacity = WorktreeCapacityAdmission(tmp_path / 'registry.sqlite3',
                     WorktreeCapacityPolicy(2, 8), disk_usage=probe_20)
    capacity.reserve(tmp_path / 'Slot')
    with pytest.raises(WorktreeCapacityRejected, match='alias'):
        capacity.is_unregistered_existing(tmp_path / 'slot')
    with sqlite3.connect(capacity.state_path) as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(WorktreeCapacityRejected, match='unknown registry'):
        capacity.is_unregistered_existing(tmp_path / 'legacy')


def test_unknown_existing_slot_does_not_reclaim_held_lease(fresh_pool):
    from datetime import UTC, datetime, timedelta
    from github_pr_feedback.controller import _pool_ledger_slot
    source, pool, receipt, namespace, _, ledger, _ = fresh_pool
    pool._slot_path(receipt, 0, namespace).mkdir(parents=True)
    now = datetime.now(UTC)
    ledger.claim_worktree_slot(_pool_ledger_slot(namespace, 0), owner_pid=9876,
                   head_sha='b' * 40, claimed_at=now, stale_before=now-timedelta(hours=10))
    slot = _pool_ledger_slot(namespace, 0)
    before = ledger._connection.execute(
        'SELECT * FROM worktree_pool_slots WHERE slot_id=?', (slot,)).fetchone()
    pool.prepare_receipt_worktree(source, receipt)
    after = ledger._connection.execute(
        'SELECT * FROM worktree_pool_slots WHERE slot_id=?', (slot,)).fetchone()
    assert tuple(before) == tuple(after)


def test_accounted_retained_slot_is_reused_in_same_namespace(fresh_pool):
    source, pool, receipt, namespace, capacity, _, calls = fresh_pool
    candidate = pool._slot_path(receipt, 0, namespace)
    with capacity.reserve(candidate):
        candidate.mkdir(parents=True)
    assert not capacity.is_unregistered_existing(candidate)
    assert pool.prepare_receipt_worktree(source, receipt).path == candidate
    assert calls[-1] == ('prepare', 0)
    with sqlite3.connect(capacity.state_path) as db:
        assert db.execute('SELECT SUM(requested_bytes) FROM reservations').fetchone() == (8,)


def test_all_unknown_slots_overflow_retains_shared_budget_gate(fresh_pool, monkeypatch):
    import github_pr_feedback.controller as controller
    source, pool, receipt, namespace, capacity, _, calls = fresh_pool
    for slot in range(2):
        pool._slot_path(receipt, slot, namespace).mkdir(parents=True)
    overflow = source.parent / 'overflow'
    capacity._probe = lambda _: SimpleNamespace(free=9)
    with pytest.raises(WorktreeCapacityRejected, match='insufficient space'):
        controller._prepare_receipt_worktree_with_overflow(pool, source, receipt, overflow)
    assert not overflow.exists()
    assert calls == []
    with sqlite3.connect(capacity.state_path) as db:
        assert db.execute('SELECT * FROM reservations').fetchall() == []


@pytest.mark.parametrize('phase', ['allocating', 'uncertain', 'wrong_device'])
def test_registered_candidate_rejection_never_skips_or_overflows(fresh_pool, phase):
    import github_pr_feedback.controller as controller
    source, pool, receipt, namespace, capacity, _, calls = fresh_pool
    candidate = pool._slot_path(receipt, 0, namespace)
    capacity.reserve(candidate)
    candidate.mkdir(parents=True)
    with sqlite3.connect(capacity.state_path) as db:
        if phase == 'wrong_device':
            db.execute("UPDATE reservations SET device='unknown',phase='retained'")
        else:
            db.execute('UPDATE reservations SET phase=?', (phase,))
    overflow = source.parent / 'overflow'
    with pytest.raises(WorktreeCapacityRejected):
        controller._prepare_receipt_worktree_with_overflow(pool, source, receipt, overflow)
    assert calls == []
    assert not overflow.exists()
    assert not pool._slot_path(receipt, 1, namespace).exists()


def test_real_git_unknown_first_slot_creates_exact_head_in_later_fresh_slot(tmp_path):
    from github_pr_feedback.controller import PooledLocalGitRepository, _pool_source_namespace, _pool_ledger_slot
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.policy import FeedbackReceipt
    source = tmp_path / 'configured-source'
    source.mkdir()
    def git(path, *args):
        result = subprocess.run(['git', '-C', str(path), *args], check=True,
                    capture_output=True, text=True, encoding='utf-8',
                    stdin=subprocess.DEVNULL, timeout=15)
        return result.stdout.strip()
    git(source, 'init')
    (source / 'owned.py').write_text('VALUE = 1\n', encoding='utf-8')
    git(source, 'add', 'owned.py')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '-m', 'test-owned source')
    head = git(source, 'rev-parse', 'HEAD')
    receipt = FeedbackReceipt('example/project', 1, 'pr_local_ci', 'real-git', head)
    capacity = WorktreeCapacityAdmission(tmp_path / 'registry.sqlite3',
                     WorktreeCapacityPolicy(2, 8), disk_usage=probe_20)
    ledger = FeedbackLedger(tmp_path / 'ledger.sqlite3')
    try:
        pool = PooledLocalGitRepository(ledger, tmp_path / 'pool', slot_count=2, capacity=capacity)
        namespace = _pool_source_namespace(source)
        unknown = pool._slot_path(receipt, 0, namespace)
        unknown.mkdir(parents=True)
        marker = unknown / 'owned.txt'
        marker.write_text('preserved owner work', encoding='utf-8')
        prepared = pool.prepare_receipt_worktree(source, receipt)
        fresh = pool._slot_path(receipt, 1, namespace)
        assert prepared.path == fresh
        assert git(fresh, 'rev-parse', 'HEAD') == head
        assert Path(git(fresh, 'rev-parse', '--show-toplevel')).resolve() == fresh.resolve()
        assert (fresh / 'owned.py').read_text(encoding='utf-8-sig') == 'VALUE = 1\n'
        assert marker.read_text(encoding='utf-8-sig') == 'preserved owner work'
        with sqlite3.connect(ledger.path) as db:
            assert db.execute('SELECT 1 FROM worktree_pool_slots WHERE slot_id=?',
                              (_pool_ledger_slot(namespace, 0),)).fetchone() is None
            assert db.execute('SELECT slot_id,status,head_sha FROM worktree_pool_slots').fetchall() == [
                (_pool_ledger_slot(namespace, 1), 'leased', head)]
        with sqlite3.connect(capacity.state_path) as db:
            assert db.execute('SELECT path,requested_bytes,phase FROM reservations').fetchall() == [
                (str(fresh), 8, 'retained')]
    finally:
        ledger.close()


def test_real_concurrent_selectors_preserve_unknown_and_share_capacity(tmp_path):
    import sys
    from github_pr_feedback.controller import PooledLocalGitRepository, _pool_source_namespace
    from github_pr_feedback.ledger import FeedbackLedger
    from github_pr_feedback.policy import FeedbackReceipt
    source = tmp_path / 'source'
    source.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(source), *args], check=True,
                  capture_output=True, text=True, encoding='utf-8',
                  stdin=subprocess.DEVNULL, timeout=15).stdout.strip()
    git('init')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '--allow-empty', '-m', 'test source')
    head = git('rev-parse', 'HEAD')
    state = tmp_path / 'registry.sqlite3'
    WorktreeCapacityAdmission(state, WorktreeCapacityPolicy(2, 8), disk_usage=probe_20)
    ledger_path = tmp_path / 'ledger.sqlite3'
    ledger = FeedbackLedger(ledger_path)
    pool_root = tmp_path / 'pool'
    receipt = FeedbackReceipt('example/project', 1, 'pr_local_ci', 'concurrent', head)
    pool = PooledLocalGitRepository(ledger, pool_root, slot_count=3)
    unknown = pool._slot_path(receipt, 0, _pool_source_namespace(source))
    unknown.mkdir(parents=True)
    marker = unknown / 'owned.txt'
    marker.write_text('preserved', encoding='utf-8')
    ledger.close()
    gate = tmp_path / 'start'
    program = '''import sys,time,json
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from github_pr_feedback.controller import PooledLocalGitRepository
from github_pr_feedback.ledger import FeedbackLedger
from github_pr_feedback.policy import FeedbackReceipt
from github_pr_feedback.worktree_capacity import WorktreeCapacityAdmission,WorktreeCapacityPolicy
plugin,source,pool,state,ledger,gate,head=sys.argv[1:]
ledger=FeedbackLedger(Path(ledger))
capacity=WorktreeCapacityAdmission(Path(state),WorktreeCapacityPolicy(2,8),disk_usage=lambda _:SimpleNamespace(free=20))
pool=PooledLocalGitRepository(ledger,Path(pool),slot_count=3,capacity=capacity)
print('ready',flush=True)
while not Path(gate).exists(): time.sleep(0.005)
try:
    result=pool.prepare_receipt_worktree(Path(source),FeedbackReceipt('example/project',1,'pr_local_ci','concurrent',head))
    print(json.dumps(str(result.path)),flush=True)
finally: ledger.close()
'''
    children = [subprocess.Popen([sys.executable, '-c', program,
                    str(Path(__file__).resolve().parents[1]), str(source), str(pool_root),
                    str(state), str(ledger_path), str(gate), head],
                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                 text=True, encoding='utf-8') for _ in range(2)]
    try:
        import queue
        import threading
        for child in children:
            ready = queue.Queue()
            reader = threading.Thread(target=lambda process=child, result=ready:
                        result.put(process.stdout.readline()), daemon=True)
            reader.start()
            try:
                line = ready.get(timeout=10)
            except queue.Empty:
                child.kill()
                _, errors = child.communicate(timeout=5)
                pytest.fail(f"selector readiness deadline exceeded: {errors}")
            assert line.strip() == 'ready', line
        gate.touch()
        paths = []
        for child in children:
            output, errors = child.communicate(timeout=20)
            assert child.returncode == 0, errors
            import json
            paths.append(Path(json.loads(output)))
        assert {path.name for path in paths} == {'slot-1','slot-2'}
        for path in paths:
            result = subprocess.run(['git','-C',str(path),'rev-parse','HEAD'], check=True,
                         capture_output=True,text=True,encoding='utf-8',stdin=subprocess.DEVNULL)
            assert result.stdout.strip() == head
        assert marker.read_text(encoding='utf-8-sig') == 'preserved'
        with sqlite3.connect(state) as db:
            assert db.execute('SELECT SUM(requested_bytes),COUNT(*) FROM reservations').fetchone() == (16, 2)
            assert db.execute("SELECT COUNT(*) FROM reservations WHERE phase='retained'").fetchone() == (2,)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill(); child.communicate(timeout=5)
