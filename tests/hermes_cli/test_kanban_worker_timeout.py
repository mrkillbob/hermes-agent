"""Worker command budgets must not turn Kanban attempts into stuck loops."""

from hermes_cli import kanban_db_dispatch as kbd


def test_worker_command_timeout_is_capped_below_attempt_runtime():
    """A long task runtime must not grant one terminal command the full lease."""
    assert kbd._worker_terminal_timeout_env(3600, None) == "600"
    assert kbd._worker_terminal_timeout_env(3600, "900") == "600"


def test_worker_command_timeout_preserves_shorter_profile_timeout():
    """A deliberately shorter profile timeout remains authoritative."""
    assert kbd._worker_terminal_timeout_env(3600, "180") is None


def test_worker_command_timeout_respects_short_attempts():
    """Short Kanban attempts still leave their grace period for terminal state."""
    assert kbd._worker_terminal_timeout_env(300, None) == "270"
