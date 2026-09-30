from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from github_pr_feedback.ledger import FeedbackLedger


class PoolClaimBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = FeedbackLedger(Path(self.temp.name) / "ledger.sqlite3")
        self.addCleanup(self.ledger._connection.close)
        self.now = datetime.now(UTC)

    def claim(self, *, now=None, owner=22):
        return self.ledger.claim_worktree_slot(1, owner_pid=owner, head_sha="a" * 40,
                                              claimed_at=now or self.now,
                                              stale_before=self.now - timedelta(hours=1))

    def row(self):
        return self.ledger._connection.execute(
            "SELECT status,owner_pid,task_id,board,lease_version FROM worktree_pool_slots WHERE slot_id=1"
        ).fetchone()

    def old_free_binding(self):
        old = self.claim()
        self.ledger.bind_worktree_slot_task("a" * 40, "old-terminal-task", "default", slot_id=1)
        self.ledger.finish_worktree_slot(old)
        return old

    def test_fresh_claim_not_visible_to_terminal_task_reconciler_before_bind(self):
        self.old_free_binding()
        new = self.claim(owner=33)
        self.assertIsNotNone(new)
        self.assertEqual(self.row()[2:4], (None, None))
        # reconcile_leases enumerates only this exact API. No historical terminal
        # binding may make the fresh, still-unbound claim eligible for release.
        self.assertEqual(self.ledger.leased_worktree_slots(), ())
        self.assertEqual(self.row()[0], "leased")

    def test_live_bound_lease_refuses_reclaim_even_when_time_is_stale(self):
        old = self.claim(now=self.now - timedelta(days=2))
        self.ledger.bind_worktree_slot_task("a" * 40, "active-task", "default", slot_id=1)
        before = self.row()
        self.assertIsNone(self.claim(owner=33))
        self.assertEqual(self.row(), before)
        self.assertEqual(self.row()[4], old.version)

    def test_old_version_cannot_finish_new_claim_same_owner(self):
        old = self.old_free_binding()
        new = self.claim()
        self.assertEqual(new.version, old.version + 1)
        self.ledger.finish_worktree_slot(old)
        self.assertEqual(self.row()[0], "leased")
        self.assertEqual(self.row()[4], new.version)

    def test_new_binding_records_only_new_task_and_board(self):
        self.old_free_binding()
        new = self.claim(owner=33)
        self.ledger.bind_worktree_slot_task("a" * 40, "new-task", "tradingbot-burndown", slot_id=1)
        slots = self.ledger.leased_worktree_slots()
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["task_id"], "new-task")
        self.assertEqual(slots[0]["board"], "tradingbot-burndown")
        self.assertEqual(slots[0]["lease_version"], new.version)
        self.assertIsNone(self.claim(owner=44))

    def test_stale_unbound_claim_clears_leftover_board_and_preserves_fence(self):
        old = self.claim(now=self.now - timedelta(days=2))
        self.ledger._connection.execute("UPDATE worktree_pool_slots SET board='old-board' WHERE slot_id=1")
        self.ledger._connection.commit()
        new = self.claim(owner=33)
        self.assertEqual(new.version, old.version + 1)
        self.assertEqual(self.row()[2:4], (None, None))
        self.ledger.finish_worktree_slot(old)
        self.assertEqual(self.row()[0], "leased")


if __name__ == "__main__":
    unittest.main()
