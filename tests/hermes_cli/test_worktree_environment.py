"""Tests for repository-scoped virtualenv links in Git worktrees."""

import subprocess
from pathlib import Path

from hermes_cli.worktree_environment import bootstrap_worktree_environments


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    return path


def _fake_python(environment: Path) -> None:
    python = environment / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)


def test_bootstrap_links_the_repository_environment_into_a_worktree(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "hermes-agent")
    _fake_python(repo / ".venv")
    target = tmp_path / "worktree"
    target.mkdir()

    linked = bootstrap_worktree_environments(
        repo, target, environment_names=(".venv",), require_python=True
    )

    assert linked == (".venv",)
    assert (target / ".venv").is_symlink()
    assert (target / ".venv").resolve() == (repo / ".venv").resolve()


def test_bootstrap_rejects_an_environment_from_another_repository(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "hermes-agent")
    foreign = _git_repo(tmp_path / "lunabot")
    _fake_python(foreign / ".venv")
    (repo / ".venv").symlink_to(foreign / ".venv", target_is_directory=True)
    target = tmp_path / "worktree"
    target.mkdir()

    linked = bootstrap_worktree_environments(
        repo, target, environment_names=(".venv",), require_python=True
    )

    assert linked == ()
    assert not (target / ".venv").exists()

