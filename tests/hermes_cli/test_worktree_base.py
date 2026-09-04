from __future__ import annotations

import subprocess
from pathlib import Path

from hermes_cli.worktree_base import resolve_worktree_base


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, filename: str, content: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", filename)
    return _git(repo, "rev-parse", "HEAD")


def _feature_checkout_with_remote_default(tmp_path: Path) -> tuple[Path, str, str]:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))

    repo = tmp_path / "repo"
    _git(tmp_path, "clone", str(remote), str(repo))
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "switch", "-c", "main")
    main_sha = _commit(repo, "main.txt", "main\n")
    _git(repo, "push", "-u", "origin", "main")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

    _git(repo, "switch", "-c", "feature")
    feature_sha = _commit(repo, "feature.txt", "feature\n")
    _git(repo, "push", "-u", "origin", "feature")
    _git(repo, "remote", "set-head", "origin", "main")
    return repo, main_sha, feature_sha


def test_new_work_resolves_remote_default_not_current_feature_upstream(tmp_path: Path) -> None:
    repo, main_sha, feature_sha = _feature_checkout_with_remote_default(tmp_path)

    base_ref, label = resolve_worktree_base(
        str(repo),
        prefer_current_upstream=False,
    )

    assert base_ref == "origin/main"
    assert "origin/main" in label
    assert _git(repo, "rev-parse", base_ref) == main_sha
    assert _git(repo, "rev-parse", base_ref) != feature_sha


def test_continuation_can_prefer_current_feature_upstream(tmp_path: Path) -> None:
    repo, _main_sha, feature_sha = _feature_checkout_with_remote_default(tmp_path)

    base_ref, _label = resolve_worktree_base(
        str(repo),
        prefer_current_upstream=True,
    )

    assert base_ref == "origin/feature"
    assert _git(repo, "rev-parse", base_ref) == feature_sha
