import subprocess

import pytest

from github_pr_feedback.git_stack import GitStackError, GitStackRunner


def test_refresh_merges_base_and_pushes_without_history_rewrite(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[-2:] == ("stack", "view"):
            return subprocess.CompletedProcess(argv, 0, "Stack #other\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    runner = GitStackRunner(tmp_path)
    runner.merge_base_into_branch("codex/child", "stable")
    runner.push_branch("codex/child")
    assert calls == [
        ("git", "-C", str(tmp_path), "fetch", "origin", "stable", "codex/child"),
        ("git", "-C", str(tmp_path), "switch", "codex/child"),
        ("git", "-C", str(tmp_path), "merge", "--no-edit", "--no-ff", "origin/stable"),
        (
            "git", "-C", str(tmp_path),
            "push", "origin", "HEAD:refs/heads/codex/child",
        )
    ]


def test_push_rejects_every_force_option(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    GitStackRunner(tmp_path).push_branch("codex/child")
    flattened = " ".join(calls[0])
    assert "--force" not in flattened
    assert "--force-with-lease" not in flattened


def test_git_failures_are_not_hidden(monkeypatch, tmp_path):
    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "non-fast-forward")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    with pytest.raises(GitStackError, match="non-fast-forward"):
        GitStackRunner(tmp_path).push_branch("codex/child")


def test_native_stack_refresh_uses_fixed_gh_stack_commands(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    runner = GitStackRunner(tmp_path, environment={"GH_TOKEN": "hidden"})
    runner.native_stack_checkout("1281")
    runner.native_stack_rebase()
    runner.native_stack_push()

    assert calls == [
        ("git", "-C", str(tmp_path), "rev-parse", "--git-path", "gh-stack"),
        ("gh", "stack", "view"),
        ("gh", "stack", "checkout", "1281"),
        ("gh", "stack", "rebase"),
        ("gh", "stack", "push"),
    ]


def test_native_stack_selector_rejects_shell_like_input(tmp_path):
    with pytest.raises(GitStackError, match="selector"):
        GitStackRunner(tmp_path).native_stack_checkout("1281;rm")


def test_native_stack_checkout_reuses_verified_local_stack_state(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[:4] == ("git", "-C", str(tmp_path), "rev-parse"):
            return subprocess.CompletedProcess(argv, 0, "gh-stack\n", "")
        return subprocess.CompletedProcess(argv, 0, "Stack #1329\n", "")

    (tmp_path / "gh-stack").write_text('{"stacks":[{"number":1329}]}', encoding="utf-8")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    runner = GitStackRunner(tmp_path)

    runner.native_stack_checkout("1329")

    assert calls == [
        ("git", "-C", str(tmp_path), "rev-parse", "--git-path", "gh-stack"),
    ]
