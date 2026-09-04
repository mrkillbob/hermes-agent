from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from plugins.hermes_revenue_lab.scripts.verify_isolation import is_policy_denial, verify_isolation


class IsolationTest(unittest.TestCase):
    def test_sandbox_apply_failure_is_not_counted_as_policy_denial(self) -> None:
        """Catches an unavailable sandbox being reported as proven isolation."""
        unavailable = subprocess.CompletedProcess(
            args=("sandbox-exec",),
            returncode=71,
            stdout="",
            stderr="sandbox-exec: sandbox_apply: Operation not permitted",
        )
        denied = subprocess.CompletedProcess(
            args=("sandbox-exec",),
            returncode=1,
            stdout="",
            stderr="PermissionError: [Errno 1] Operation not permitted",
        )

        self.assertFalse(is_policy_denial(unavailable))
        self.assertTrue(is_policy_denial(denied))

    @unittest.skipUnless(
        sys.platform == "darwin", "requires macOS sandbox-exec"
    )
    def test_sandbox_allows_lab_write_and_denies_outside_write_open(self) -> None:
        """Catches a policy that either breaks the lab or permits external writes."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lab = root / "lab"
            outside = root / "outside-repo"
            lab.mkdir()
            outside.mkdir()
            probe = outside / "README.md"
            probe.write_text("unchanged\n")
            subprocess.run(("git", "init", "-q"), cwd=outside, check=True)
            subprocess.run(("git", "config", "user.name", "Test"), cwd=outside, check=True)
            subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=outside, check=True)
            subprocess.run(("git", "add", "README.md"), cwd=outside, check=True)
            subprocess.run(("git", "commit", "-qm", "seed"), cwd=outside, check=True)

            verdict = verify_isolation(lab, outside, probe)

            self.assertTrue(verdict["inside_write_allowed"])
            self.assertTrue(verdict["tradingbot_write_denied"])
            self.assertTrue(verdict["probe_hash_unchanged"])
            self.assertTrue(verdict["git_status_unchanged"])
            self.assertEqual("unchanged\n", probe.read_text())


if __name__ == "__main__":
    unittest.main()
