"""Real Git consumer checkouts retain tested ancestry without unrelated refs."""

import os
from pathlib import Path
import subprocess

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms("linux")


def _git(cwd, *args, input=None):
    env = {
        "PATH": os.defpath, "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1",
    }
    return subprocess.run(
        ["git", "-c", "user.name=CI Fixture", "-c", "user.email=ci@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd, env=env, input=input, text=True, capture_output=True,
        timeout=30, check=True,
    ).stdout.strip()


@pytest.mark.parametrize("commit_count", [1, 2])
def test_desktop_install_keeps_complete_tested_history_without_unrelated_objects(tmp_path, commit_count):
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "--initial-branch=main")
    names = ("base", "head")[:commit_count]
    for name in names:
        (seed / f"{name}.txt").write_text(f"{name} payload\n", encoding="utf-8")
        _git(seed, "add", ".")
        _git(seed, "commit", "-qm", name)
    tested = _git(seed, "rev-parse", "HEAD")
    base = _git(seed, "rev-list", "--max-parents=0", "HEAD")
    blob = _git(seed, "hash-object", "-w", "--stdin", input="unrelated synthetic asset\n")
    tree = _git(seed, "mktree", input=f"100644 blob {blob}\tunrelated.bin\n")
    unrelated = _git(seed, "commit-tree", tree, input="unrelated root\n")
    _git(seed, "update-ref", "refs/heads/unrelated", unrelated)
    _git(seed, "update-ref", "refs/tags/unrelated-release", unrelated)

    workflow = YAML(typ="base").load(
        (ROOT / ".github/workflows/e2e-desktop-update.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["update"]["steps"]
    settings = next(step["with"] for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-q")
    _git(checkout, "remote", "add", "origin", seed.as_uri())
    depth = int(settings.get("fetch-depth", 1))
    if depth:
        _git(checkout, "fetch", "-q", "--no-tags", f"--depth={depth}", "origin", tested)
    else:
        _git(checkout, "fetch", "-q", "--no-tags", "origin",
             "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*")
    _git(checkout, "checkout", "-q", "--detach", tested)
    for step in steps:
        if step.get("name") == "Fetch complete tested-commit ancestry":
            for _ in range(2):
                subprocess.run(
                    ["bash", "-eu", "-o", "pipefail", "-c", step["run"]],
                    cwd=checkout,
                    env={"PATH": os.defpath, "HOME": str(tmp_path),
                         "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                         "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"},
                    capture_output=True, text=True, timeout=30, check=True,
                )
    assert _git(checkout, "rev-parse", "HEAD") == tested
    assert _git(checkout, "rev-parse", "--is-shallow-repository") == "false"
    assert _git(checkout, "rev-list", "--count", "HEAD") == str(commit_count)
    assert _git(checkout, "cat-file", "-t", base) == "commit"
    assert _git(checkout, "cat-file", "--batch-check", input=unrelated + "\n") == f"{unrelated} missing"
    assert _git(checkout, "cat-file", "--batch-check", input=blob + "\n") == f"{blob} missing"
    assert _git(checkout, "tag", "--list") == ""

    origin = tmp_path / "consumer-origin.git"
    _git(tmp_path, "clone", "-q", "--bare", "--shared", "--no-tags", "--single-branch", str(checkout), str(origin))
    _git(origin, "update-ref", "refs/heads/main", tested)
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    installed = tmp_path / "installed"
    _git(tmp_path, "clone", "-q", "--no-tags", origin.as_uri(), str(installed))
    assert _git(installed, "rev-parse", "HEAD") == tested
    assert _git(installed, "rev-list", "--count", "HEAD") == str(commit_count)
    for name in names:
        assert (installed / f"{name}.txt").read_text(encoding="utf-8") == f"{name} payload\n"
    publisher = tmp_path / "publisher"
    _git(origin, "worktree", "add", "-q", "--detach", str(publisher), "main")
    (publisher / "updated.txt").write_text("forward update\n", encoding="utf-8")
    _git(publisher, "add", "updated.txt")
    _git(publisher, "commit", "-qm", "published update")
    published = _git(publisher, "rev-parse", "HEAD")
    _git(publisher, "push", "-q", str(origin), "HEAD:main")
    _git(installed, "fetch", "-q", "--no-tags", "origin", "main")
    _git(installed, "merge", "-q", "--ff-only", "FETCH_HEAD")
    assert _git(installed, "rev-parse", "HEAD") == published
    assert _git(installed, "rev-list", "--count", "HEAD") == str(commit_count + 1)
    assert (installed / "updated.txt").read_text(encoding="utf-8") == "forward update\n"
