import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.environments.local import build_subprocess_env

from github_pr_feedback.pool_doctor import classify, inventory, read_ledger


def proof():
    return {"git_valid": True, "clean_including_untracked": True,
            "no_unpushed_commits": True, "board_inventory_complete": True,
            "descendants_finished": True, "source_identity_known": True,
            "config_reference": False, "process_reference": False,
            "board_statuses": ["done"], "lease_binding_resolved": True,
            "logical_bytes": 123, "source_identity": "/source"}


def make_directory_link(path, target):
    if os.name == "nt":
        # Native directory junctions require no symlink privilege or elevation.
        subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(path), str(target)],
                       check=True, capture_output=True, text=True)
    else:
        path.symlink_to(target, target_is_directory=True)


def remove_directory_link(path):
    if os.name == "nt":
        os.rmdir(path)
    else:
        path.unlink()


class PoolDoctorTests(unittest.TestCase):
    def test_free_lease_without_positive_proof_stays_protected(self):
        reasons = classify({"status": "free"}, {}, ledger_available=True)
        self.assertIn("git_valid_not_proven", reasons)
        self.assertIn("process_reference_present_or_unknown", reasons)

    def test_each_ownership_and_git_guard_blocks_review(self):
        for key, value in [("git_valid", False), ("clean_including_untracked", False),
                           ("no_unpushed_commits", False), ("descendants_finished", False),
                           ("board_inventory_complete", False), ("config_reference", True),
                           ("process_reference", True), ("source_identity_known", False),
                           ("board_statuses", ["blocked"]), ("board_statuses", [None]),
                           ("board_statuses", [{}]), ("lease_binding_resolved", False)]:
            with self.subTest(key=key):
                p = proof(); p[key] = value
                self.assertTrue(classify({"status": "free", "task_id": "t"}, p,
                                         ledger_available=True))

    def test_missing_leased_unknown_and_binding_remain_protected(self):
        for lease in [None, {"status": "leased"}, {"status": "unknown"}]:
            with self.subTest(lease=lease):
                self.assertTrue(classify(lease, proof(), ledger_available=True))
        self.assertIn("ledger_unknown", classify(None, proof(), ledger_available=False))

    def test_readonly_sqlite_failure_does_not_create_or_modify_database(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "missing.sqlite3"
            self.assertFalse(read_ledger(p)["available"])
            self.assertFalse(p.exists())
            with sqlite3.connect(p) as c:
                c.execute("CREATE TABLE worktree_pool_slots(slot_id INTEGER,status TEXT,owner_pid INTEGER,task_id TEXT,board TEXT,lease_version INTEGER)")
                c.execute("INSERT INTO worktree_pool_slots VALUES(1,'leased',12,'t','default',1)")
            before = p.read_bytes()
            self.assertEqual(read_ledger(p)["rows"][0]["status"], "leased")
            self.assertEqual(before, p.read_bytes())
            bad = Path(temp) / "bad.sqlite3"; bad.write_bytes(b"not sqlite")
            self.assertFalse(read_ledger(bad)["available"])
            self.assertEqual(bad.read_bytes(), b"not sqlite")

    def make_slot(self, root, identity, slot_id=0, repository="repo-" + "a" * 16):
        ns = hashlib.sha256(identity.encode()).hexdigest()[:16]
        slot = root / repository / ("source-" + ns) / ("slot-" + str(slot_id))
        slot.mkdir(parents=True)
        lease_id = (int(ns, 16) % 100_000_000) * 32 + slot_id
        return slot, lease_id

    def test_global_generation_accounting_and_candidate_never_authorizes_delete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); a, aid = self.make_slot(root, "/source")
            b, bid = self.make_slot(root, "/old-source")
            snap = {"available": True, "rows": [{"slot_id": aid, "status": "free"},
                                                 {"slot_id": bid, "status": "free"}]}
            result = inventory(root, snap, {str(a): proof()})
            self.assertEqual(len(result["generations"]), 2)
            self.assertEqual(len(result["slots"]), 2)
            candidate = next(r for r in result["slots"] if r["path"] == str(a))
            self.assertEqual(candidate["disposition"], "OWNER_REVIEW_CANDIDATE")
            self.assertFalse(candidate["deletion_authorized"])
            self.assertFalse(result["destructive_actions_supported"])
            self.assertEqual(sum(g["known_logical_bytes"] for g in result["generations"]), 123)
            self.assertEqual(sum(g["unknown_size_slots"] for g in result["generations"]), 1)
            self.assertEqual(next(r for r in result["slots"] if r["path"] == str(b))["disposition"], "PRESERVE")

    def test_symlinks_not_traversed_and_venv_target_not_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pool"; slot, lid = self.make_slot(root, "/source")
            outside = Path(temp) / "outside"; outside.mkdir()
            make_directory_link(slot / ".venv", outside)
            snap = {"available": True, "rows": [{"slot_id": lid, "status": "free"}]}
            result = inventory(root, snap)
            self.assertEqual(result["slots"][0]["venv_link_target"], str(outside))
            self.assertIsNone(result["slots"][0]["logical_bytes"])
            # Replace the whole slot with a link; never inspect its contents.
            remove_directory_link(slot / ".venv"); slot.rmdir(); make_directory_link(slot, outside)
            result = inventory(root, snap, {str(slot): proof()})
            self.assertIn("symlink_not_traversed", result["slots"][0]["protection_reasons"])
            self.assertIsNone(result["slots"][0]["venv_link_target"])
            make_directory_link(root / ("repo-" + "b" * 16), outside)
            self.assertTrue(inventory(root, snap)["issues"])
            alias = Path(temp) / "alias"; make_directory_link(alias, root)
            self.assertEqual(inventory(alias, snap)["issues"], ["pool_root_symlink"])

    def test_ambiguous_namespace_ledger_identity_is_protected_globally(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); a, lid = self.make_slot(root, "/source")
            b, _ = self.make_slot(root, "/source", repository="repo-" + "b" * 16)
            result = inventory(root, {"available": True, "rows": [{"slot_id": lid, "status": "free"}]},
                               {str(a): proof(), str(b): proof()})
            self.assertIn("ambiguous_ledger_slot_identity", result["issues"])
            self.assertTrue(all(r["disposition"] == "PRESERVE" for r in result["slots"]))

    def test_source_identity_mismatch_and_unavailable_inventory_remain_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); slot, lid = self.make_slot(root, "/other")
            result = inventory(root, {"available": True, "rows": [{"slot_id": lid, "status": "free"}]},
                               {str(slot): proof()})
            self.assertIn("source_identity_hash_unresolved", result["slots"][0]["protection_reasons"])
            self.assertIn("inventory_incomplete", inventory(root / "missing", {"available": False})["issues"])

    def test_cli_reads_utf8_bom_evidence_without_locale_assumptions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = root / "evidence.json"
            evidence.write_bytes(b"\xef\xbb\xbf{}")
            result = subprocess.run(
                [sys.executable, "-m", "github_pr_feedback.pool_doctor",
                 "--pool-root", str(root), "--ledger", str(root / "missing.sqlite3"),
                 "--evidence", str(evidence)],
                check=True, capture_output=True, text=True,
                env=build_subprocess_env(
                    inherit_profile_home=False,
                    extra={"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                ),
            )
            self.assertFalse(json.loads(result.stdout)["destructive_actions_supported"])


if __name__ == "__main__":
    unittest.main()
