"""Source checkout identity is written only from the checkout itself."""

import json
import os
from pathlib import Path
import subprocess

import pytest


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True,
        env={"HOME": str(repo.parent), "PATH": os.environ["PATH"]},
    )
    return result.stdout.strip()


def test_write_source_stamp_records_live_checkout_identity_atomically(tmp_path):
    from hermes_cli.source_stamp import write_source_stamp

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Hermes Test")
    _git(repo, "config", "user.email", "hermes@example.invalid")
    (repo / "tracked").write_text("release\n", encoding="utf-8")
    _git(repo, "add", "tracked")
    _git(repo, "commit", "-qm", "release")
    _git(repo, "tag", "v0.21.4")
    (repo / "tracked").write_text("next\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "next")

    written = write_source_stamp(repo)
    stored = json.loads((repo / "install-stamp.json").read_text(encoding="utf-8-sig"))

    assert stored == written
    assert stored["commit"] == _git(repo, "rev-parse", "HEAD")
    assert stored["baseVersion"] == "0.21.4"
    assert stored["displayVersion"].startswith("0.21.4+1.g")
    assert stored["source"] == "git"
    assert stored["distribution"] is None
    assert stored["updateMechanism"] == "self"
    assert not list(repo.glob(".install-stamp.*.tmp"))


def test_stale_source_stamp_defers_to_live_checkout(tmp_path, monkeypatch):
    from hermes_cli.source_stamp import write_source_stamp
    from hermes_cli.version_info import _reset_version_info_cache, get_version_info

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Hermes Test")
    _git(repo, "config", "user.email", "hermes@example.invalid")
    (repo / "tracked").write_text("release\n", encoding="utf-8")
    _git(repo, "add", "tracked")
    _git(repo, "commit", "-qm", "release")
    _git(repo, "tag", "v0.21.4")
    write_source_stamp(repo)

    (repo / "tracked").write_text("manual pull\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "manual pull")
    monkeypatch.setenv("HERMES_INSTALL_ROOT", str(repo))
    monkeypatch.setattr("hermes_cli.version_info._resolve_repo_dir", lambda: repo)
    _reset_version_info_cache()

    info = get_version_info()

    assert info.commit == _git(repo, "rev-parse", "HEAD")
    assert info.derived_version.startswith("0.21.4+1.g")
    assert info.source == "git"

def _committed_parent(tmp_path: Path) -> Path:
    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init", "-q")
    _git(parent, "config", "user.name", "Hermes Test")
    _git(parent, "config", "user.email", "hermes@example.invalid")
    (parent / "tracked").write_text("parent release\n", encoding="utf-8")
    _git(parent, "add", "tracked")
    _git(parent, "commit", "-qm", "parent release")
    return parent


@pytest.mark.parametrize("invalid_git", [False, True], ids=["no-git", "empty-dotgit"])
def test_source_stamp_never_borrows_an_enclosing_checkout_identity(tmp_path, invalid_git):
    from hermes_cli.source_stamp import write_source_stamp

    parent = _committed_parent(tmp_path)
    write_source_stamp(parent)
    parent_stamp = (parent / "install-stamp.json").read_bytes()
    child = parent / "unpacked-install"
    child.mkdir()
    if invalid_git:
        (child / ".git").mkdir()
    # A replaced ZIP tree must clear its old identity, not borrow the parent's
    # HEAD or advance its installer receipt to an unrelated commit.
    (child / "install-stamp.json").write_text('{"commit": "old"}', encoding="utf-8")
    receipt = child / ".hermes-bootstrap-complete"
    receipt.write_text('{"pinnedCommit": "old"}', encoding="utf-8")
    before_receipt = receipt.read_bytes()

    assert write_source_stamp(child) is None

    assert not (child / "install-stamp.json").exists()
    assert receipt.read_bytes() == before_receipt
    assert (parent / "install-stamp.json").read_bytes() == parent_stamp


def test_source_stamp_accepts_linked_worktree_identity(tmp_path):
    from hermes_cli.source_stamp import write_source_stamp

    parent = _committed_parent(tmp_path)
    worktree = tmp_path / "linked"
    _git(parent, "worktree", "add", "--detach", str(worktree), "HEAD")
    assert (worktree / ".git").is_file()

    written = write_source_stamp(worktree)

    assert written is not None
    assert written["commit"] == _git(worktree, "rev-parse", "HEAD")
    assert json.loads((worktree / "install-stamp.json").read_text()) == written


@pytest.mark.platforms("posix")
def test_source_stamp_accepts_symlinked_checkout_root(tmp_path):
    from hermes_cli.source_stamp import write_source_stamp

    parent = _committed_parent(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)

    written = write_source_stamp(alias)

    assert written is not None
    assert written["commit"] == _git(parent, "rev-parse", "HEAD")
    assert json.loads((parent / "install-stamp.json").read_text()) == written


@pytest.mark.platforms("posix")
def test_source_stamp_preserves_trailing_whitespace_in_checkout_path(tmp_path):
    from hermes_cli.source_stamp import write_source_stamp

    repo = _committed_parent(tmp_path).rename(tmp_path / "repo ")
    (repo / "install-stamp.json").write_text('{"commit": "old"}', encoding="utf-8")

    written = write_source_stamp(repo)

    assert written is not None
    assert written["commit"] == _git(repo, "rev-parse", "HEAD")
    assert json.loads((repo / "install-stamp.json").read_text()) == written
