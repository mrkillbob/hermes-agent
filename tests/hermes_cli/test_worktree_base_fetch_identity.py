"""A fetch of another remote/branch cannot refresh the selected base's age."""
import subprocess

import pytest


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
         "-C", str(repo), *args],
        check=True, capture_output=True, text=True, timeout=20,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


@pytest.fixture(params=["path", "uri"])
def remote_case(tmp_path, request):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "-q", "--initial-branch=main")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "original")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", origin.as_uri() if request.param == "uri" else str(origin), str(checkout))
    _git(checkout, "fetch", "origin", "main")
    original = _git(checkout, "rev-parse", "origin/main")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "new-main")
    current = _git(origin, "rev-parse", "HEAD")
    assert current != original
    return origin, checkout, original, current


def test_other_remotes_same_branch_does_not_make_origin_fresh(remote_case, tmp_path):
    from hermes_cli.worktree_base import resolve_worktree_base

    _origin, checkout, _original, current = remote_case
    upstream = tmp_path / "upstream.git"
    upstream.mkdir()
    _git(upstream, "init", "-q", "--initial-branch=main")
    _git(upstream, "commit", "-q", "--allow-empty", "-m", "other-remote")
    _git(checkout, "remote", "add", "upstream", str(upstream))
    _git(checkout, "fetch", "upstream", "main")
    ref, _ = resolve_worktree_base(str(checkout), prefer_current_upstream=False)
    assert ref == "origin/main"
    assert _git(checkout, "rev-parse", ref) == current


def test_append_of_other_branch_does_not_make_old_main_fresh(remote_case):
    from hermes_cli.worktree_base import resolve_worktree_base

    origin, checkout, _original, current = remote_case
    _git(origin, "branch", "feature", "HEAD")
    _git(checkout, "fetch", "--append", "origin", "feature")
    ref, _ = resolve_worktree_base(str(checkout), prefer_current_upstream=False)
    assert ref == "origin/main"
    assert _git(checkout, "rev-parse", ref) == current


def test_same_remote_and_branch_reuses_recent_fetch(remote_case):
    from hermes_cli.worktree_base import resolve_worktree_base

    _origin, checkout, _original, current = remote_case
    _git(checkout, "fetch", "origin", "main")
    ref, provenance = resolve_worktree_base(str(checkout), prefer_current_upstream=False)
    assert ref == "origin/main"
    assert _git(checkout, "rev-parse", ref) == current
    assert provenance.startswith("origin/main (fetched ") and provenance.endswith("s ago)")


def test_recent_fetch_must_match_the_current_tracking_ref(remote_case):
    from hermes_cli.worktree_base import resolve_worktree_base

    _origin, checkout, original, current = remote_case
    _git(checkout, "fetch", "origin", "main")
    _git(checkout, "update-ref", "refs/remotes/origin/main", original)
    ref, _ = resolve_worktree_base(str(checkout), prefer_current_upstream=False)
    assert ref == "origin/main"
    assert _git(checkout, "rev-parse", ref) == current
