"""Publishing a fixture release reuses its origin's objects and leaves no writer."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tests.e2e.core.upgrade import _install_helpers as installer


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.name", "fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    for number in range(40):
        (source / f"unchanged-{number}.txt").write_text(f"base content {number}\n", encoding="utf-8")
    git(source, "add", ".")
    git(source, "commit", "-qm", "base")
    git(source, "commit", "--allow-empty", "-qm", "tip")
    return source


@pytest.mark.parametrize("shallow", [False, True])
def test_release_publication_preserves_tree_without_repacking_existing_objects(origin, tmp_path, monkeypatch, shallow):
    bare = tmp_path / "origin.git"
    arguments = ["clone", "-q", "--bare"]
    arguments += ["--depth=1", origin.as_uri()] if shallow else ["--shared", str(origin)]
    git(tmp_path, *arguments, str(bare))
    base = git(bare, "rev-parse", "main")
    tree = git(bare, "ls-tree", "-r", "main").splitlines()
    trace = tmp_path / "git-events.jsonl"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    scratch = tmp_path / "publisher with spaces"
    scratch.mkdir()

    target = installer.publish_commit(bare, scratch, "next release", {"RELEASE.txt": "new release\n"})

    assert git(bare, "rev-parse", "main") == target
    assert git(bare, "rev-parse", f"{target}^") == base
    assert git(bare, "show", f"{target}:RELEASE.txt") == "new release"
    assert set(tree).issubset(git(bare, "ls-tree", "-r", target).splitlines())
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8-sig").splitlines()]
    packed = [int(event["value"]) for event in events if event.get("key") == "write_pack_file/wrote"]
    # One new blob, tree and commit may be transferred. Existing release
    # contents are already in the local origin and must not be packed again.
    assert all(count <= 3 for count in packed), packed
    assert git(bare, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not any(scratch.iterdir())


def test_failed_publication_preserves_origin_and_releases_its_writer(origin, tmp_path, monkeypatch):
    bare = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", "--shared", str(origin), str(bare))
    base = git(bare, "rev-parse", "main")
    scratch = tmp_path / "publisher"
    scratch.mkdir()
    command = installer.git

    def fail_commit(*arguments, **options):
        if arguments[0] == "commit":
            raise AssertionError("commit refused")
        return command(*arguments, **options)

    monkeypatch.setattr(installer, "git", fail_commit)
    with pytest.raises(AssertionError, match="commit refused"):
        installer.publish_commit(bare, scratch, "refused release", {"RELEASE.txt": "refused\n"})
    assert git(bare, "rev-parse", "main") == base
    assert git(bare, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not any(scratch.iterdir())
