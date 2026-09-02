import subprocess

import pytest

from github_pr_feedback.git_stack import GitStackError, GitStackRunner


def test_push_uses_force_with_lease(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    GitStackRunner(tmp_path).push_branch("codex/child", "a" * 40)
    assert calls == [
        (
            "git", "-C", str(tmp_path),
            "push", f"--force-with-lease=refs/heads/codex/child:{'a' * 40}",
            "origin", "HEAD:refs/heads/codex/child",
        )
    ]


def test_git_failures_are_not_hidden(monkeypatch, tmp_path):
    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "non-fast-forward")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    with pytest.raises(GitStackError, match="non-fast-forward"):
        GitStackRunner(tmp_path).push_branch("codex/child", "a" * 40)
