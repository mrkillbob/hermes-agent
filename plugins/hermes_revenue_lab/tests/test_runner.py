import unittest

from hermes_revenue_lab.inventory.runner import run_command
from hermes_revenue_lab.inventory.types import CommandSpec


class RunnerTest(unittest.TestCase):
    def test_timeout_is_unavailable_and_has_no_fake_exit_code(self) -> None:
        """Catches timeouts being reported as successful zero-exit commands."""
        result = run_command(CommandSpec("timeout", ("/bin/sleep", "1"), 0.01))

        self.assertEqual("unavailable", result.status)
        self.assertIsNone(result.exit_code)

    def test_argv_execution_does_not_use_a_shell(self) -> None:
        """Catches command arguments being evaluated through a shell."""
        result = run_command(CommandSpec("literal", ("/bin/echo", "$(id)")))

        self.assertEqual("available", result.status)
        self.assertEqual("$(id)", result.stdout.strip())

    def test_missing_executable_is_explicitly_unavailable(self) -> None:
        """Catches absent optional tooling being mistaken for a logic failure."""
        result = run_command(CommandSpec("missing", ("/definitely/not/installed",)))

        self.assertEqual("unavailable", result.status)
        self.assertIsNone(result.exit_code)
        self.assertEqual("not installed", result.stderr)


if __name__ == "__main__":
    unittest.main()
