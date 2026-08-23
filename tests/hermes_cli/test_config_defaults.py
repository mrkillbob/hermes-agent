from hermes_cli.config import DEFAULT_CONFIG


def test_conversation_worktree_defaults_are_platform_neutral_and_disabled():
    worktree = DEFAULT_CONFIG["conversation_worktree"]

    assert worktree["enabled"] is False
    assert worktree["source_worktree"] is None
    assert worktree["worktree_root"] is None
    assert worktree["retain_until_explicit_cleanup"] is True
