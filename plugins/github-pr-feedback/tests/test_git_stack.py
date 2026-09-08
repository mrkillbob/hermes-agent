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


def test_branch_rejects_option_like_names(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("github_pr_feedback.stack._run", fake_run)
    with pytest.raises(ValueError, match="safe branch name"):
        GitStackRunner(tmp_path).merge_base_into_branch("-f", "stable")
    assert calls == []


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
        if argv[3:] == ("rev-list", "--first-parent", "HEAD"):
            stdout = "a" * 40 + "\n"
        if argv[3:] == ("rev-parse", "--git-path", "objects"):
            stdout = str(tmp_path / "objects")
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    GitStackRunner(tmp_path).push_verified_head("acme/widgets", "codex/child", "a" * 40)
    assert calls[:4] == [
        ("git", "-C", str(tmp_path), "rev-parse", "HEAD"),
        (
            "git", "-C", str(tmp_path), "merge-base", "--is-ancestor",
            "a" * 40, "HEAD",
        ),
        (
            "git", "-C", str(tmp_path), "rev-list", "--first-parent", "HEAD",
        ),
        (
            "git", "-C", str(tmp_path), "rev-parse", "--git-path", "objects",
        ),
    ]
    isolated = calls[4][2]
    assert calls[4] == ("git", "-C", isolated, "init", "--bare", "--quiet")
    assert calls[5] == (
        "git", "-C", isolated, "update-ref", "refs/heads/hermes-push", "b" * 40
    )
    assert calls[8] == (
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


def test_push_verified_head_lease_rejects_a_remote_advance(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    subprocess.run(["git", "init", "--quiet", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "Test"], check=True)
    (work / "file").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "file"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "--quiet", "-m", "a"], check=True)
    first = subprocess.check_output(["git", "-C", str(work), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(work), "push", "--quiet", str(remote), "HEAD:refs/heads/main"], check=True)
    (work / "file").write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "commit", "--quiet", "-am", "b"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "--quiet", str(remote), "HEAD:refs/heads/main"], check=True)
    (work / "file").write_text("c\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "commit", "--quiet", "-am", "c"], check=True)
    result = subprocess.run(
        [
            "git", "-C", str(work), "push", str(remote),
            f"--force-with-lease=refs/heads/main:{first}",
            "HEAD:refs/heads/main",
        ], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "stale info" in result.stderr


def test_push_verified_head_uploads_lfs_objects_before_the_git_ref(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[3:] == ("init", "--bare", "--quiet"):
            (Path(argv[2]) / "objects" / "info").mkdir(parents=True, exist_ok=True)
        if argv[3:] == ("rev-parse", "HEAD"):
            stdout = "b" * 40
        elif argv[3:] == ("rev-parse", "--git-path", "objects"):
            stdout = str(tmp_path / "objects")
        elif argv[3:] == ("rev-list", "--first-parent", "HEAD"):
            stdout = "a" * 40 + "\n"
        elif argv[3:] == ("rev-parse", "--git-path", "lfs"):
            stdout = str(tmp_path / "lfs")
        elif argv[3:] == ("check-attr", "--cached", "--stdin", "-z", "filter"):
            stdout = "large.bin\0filter\0lfs\0"
        else:
            stdout = ""
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr("github_pr_feedback.git_stack.subprocess.run", fake_run)
    GitStackRunner(tmp_path).push_verified_head("acme/widgets", "codex/child", "a" * 40)
    isolated = calls[4][2]
    assert calls[4] == ("git", "-C", isolated, "init", "--bare", "--quiet")
    assert calls[9] == (
        "git", "-C", isolated,
        "-c", f"lfs.storage={tmp_path / 'lfs'}", "lfs", "push",
        "https://github.com/acme/widgets.git", "refs/heads/hermes-push",
    )
    assert calls[10][3] == "push"
