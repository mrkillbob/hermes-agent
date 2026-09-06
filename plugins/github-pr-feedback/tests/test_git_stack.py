import subprocess
from pathlib import Path

import pytest

from github_pr_feedback.git_stack import GitStackError, GitStackRunner


def test_refresh_merges_base_and_pushes_without_history_rewrite(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
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


def test_push_verified_head_uses_canonical_repository_and_exact_local_head(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[3:] == ("init", "--bare", "--quiet"):
            (Path(argv[2]) / "objects" / "info").mkdir(parents=True, exist_ok=True)
        stdout = "b" * 40 if argv[3:] == ("rev-parse", "HEAD") else ""
        if argv[3:] == ("rev-parse", "--git-path", "objects"):
            stdout = str(tmp_path / "objects")
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    GitStackRunner(tmp_path).push_verified_head("acme/widgets", "codex/child", "a" * 40)
    assert calls[:3] == [
        ("git", "-C", str(tmp_path), "rev-parse", "HEAD"),
        (
            "git", "-C", str(tmp_path), "merge-base", "--is-ancestor",
            "a" * 40, "HEAD",
        ),
        (
            "git", "-C", str(tmp_path), "rev-parse", "--git-path", "objects",
        ),
    ]
    isolated = calls[3][2]
    assert calls[3] == ("git", "-C", isolated, "init", "--bare", "--quiet")
    assert calls[4] == (
        "git", "-C", isolated, "update-ref", "refs/heads/hermes-push", "b" * 40
    )
    assert calls[5] == (
        "git", "-C", isolated, "push",
        "https://github.com/acme/widgets.git",
        "--force-with-lease=refs/heads/codex/child:" + "a" * 40,
        "refs/heads/hermes-push:refs/heads/codex/child",
    )


def test_push_verified_head_rejects_a_non_descendant_without_pushing(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[-4:-2] == ("merge-base", "--is-ancestor"):
            return subprocess.CompletedProcess(argv, 1, "", "not an ancestor")
        stdout = "b" * 40 if argv[-2:] == ("rev-parse", "HEAD") else ""
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    with pytest.raises(GitStackError, match="not an ancestor"):
        GitStackRunner(tmp_path).push_verified_head("acme/widgets", "codex/child", "a" * 40)
    assert not any(call[3] == "push" for call in calls)
