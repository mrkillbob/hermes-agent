"""Long checkout diagnostics retain the fatal tail without disclosing credentials."""

from agent.conversation_worktree import ConversationWorktreeManager


def test_progress_flood_retains_redacted_diagnostic_head_and_fatal_tail():
    diagnostic = (
        "token=head-secret https://head-user:head-pass@example.invalid/repo\x00\r\n"
        + "Updating files: 11% (100/900)\r" * 200
        + "fatal: checkout failed password=tail-secret "
        "ssh://tail-user:tail-pass@example.invalid/repo\x07"
    )

    result = ConversationWorktreeManager._sanitize_diagnostic(diagnostic)

    assert "fatal: checkout failed" in result
    assert "token=<redacted>" in result
    assert "password=<redacted>" in result
    assert "https://<redacted>@example.invalid/repo" in result
    assert "ssh://<redacted>@example.invalid/repo" in result
    assert all(secret not in result for secret in (
        "head-secret", "head-user", "head-pass", "tail-secret", "tail-user", "tail-pass",
    ))
    assert not any(ord(char) < 32 or ord(char) == 127 for char in result)
    assert len(result) <= 300


def test_short_worktree_diagnostic_keeps_existing_sanitization():
    assert ConversationWorktreeManager._sanitize_diagnostic(
        "fatal:\r\n bad checkout\x00 token=secret"
    ) == "fatal: bad checkout token=<redacted>"
