"""Execute metadata gates from their declared sparse checkout, with a complete Git index."""

from collections import Counter
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest
from ruamel.yaml import YAML

from tests.ci import _gha_expr as gha

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms("linux")

_JOBS = {
    "aggregate": ("ci.yaml", "all-checks-pass", "Evaluate job results"),
    "contributor": ("contributor-check.yml", "check-attribution", "Check for unmapped contributor emails"),
    "history": ("history-check.yml", "check-common-ancestor", "Reject PRs with no common ancestor on main"),
    "infographic": ("infographic-check.yml", "check-no-committed-infographics", "Reject committed PR-infographic images"),
    "case": ("case-collision-check.yml", "check-case-collisions", "Run case-collision checker"),
}
_SCRIPTS = (
    "scripts/ci/required_results.py",
    "scripts/check-case-collisions.py",
    "scripts/releases/authors_legacy.py",
)
_EMAIL = "fresh-checkout@example.invalid"
_MAPPING = f"contributors/emails/{_EMAIL}"


def _git(cwd, *args, input=None):
    env = {
        "PATH": os.defpath, "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    command = [
        "git", "-c", "user.name=CI Fixture", "-c", f"user.email={_EMAIL}",
        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
        "-c", "core.ignoreCase=false", *args,
    ]
    return subprocess.run(
        command, cwd=cwd, env=env, input=input, text=True, capture_output=True,
        timeout=30, check=True,
    ).stdout


def _unmapped(seed):
    _git(seed, "rm", "-q", _MAPPING)


def _unrelated(seed):
    _git(seed, "checkout", "-q", "--orphan", "unrelated")


def _infographic(seed):
    path = seed / "nested" / "Infograficos" / "review.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic image")
    _git(seed, "add", "nested/Infograficos/review.png")


def _collision(seed):
    blob = _git(seed, "hash-object", "-w", "--stdin", input="synthetic\n").strip()
    for path in ("collision/Case.txt", "collision/case.txt"):
        _git(seed, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")


_REJECT = {
    "contributor": _unmapped, "history": _unrelated,
    "infographic": _infographic, "case": _collision,
}


def _checkout(tmp_path, settings, kind, result):
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "--initial-branch=main")
    for relative in _SCRIPTS:
        target = seed / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    mapping = seed / _MAPPING
    mapping.parent.mkdir(parents=True)
    mapping.write_text("fixture-user\n", encoding="utf-8")
    payload = seed / "assets" / "unneeded.bin"
    payload.parent.mkdir()
    payload.write_bytes(b"x" * 65536)
    _git(seed, "add", ".")
    _git(seed, "commit", "-qm", "base fixture")
    _git(seed, "checkout", "-qb", "feature")
    (seed / "feature.txt").write_text("fixture head\n", encoding="utf-8")
    _git(seed, "add", "feature.txt")
    if result != "success" and kind in _REJECT:
        _REJECT[kind](seed)
    _git(seed, "commit", "-qm", "head fixture")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", "--no-local", "--no-checkout", str(seed), str(checkout))
    patterns = settings.get("sparse-checkout")
    if patterns is not None:
        mode = "--no-cone" if str(settings.get("sparse-checkout-cone-mode", True)).lower() == "false" else "--cone"
        _git(checkout, "sparse-checkout", "init", mode)
        _git(checkout, "sparse-checkout", "set", mode, "--stdin", input=patterns + "\n")
    _git(checkout, "checkout", "-q", "--detach", "HEAD")
    assert _git(checkout, "ls-files", "-z") == _git(seed, "ls-files", "-z")
    assert not (checkout / "assets/unneeded.bin").exists(), "metadata job materialized unrelated payload"
    return checkout


def _ports(tmp_path, checkout, result):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrappers = {
        "python3": f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -S -B \"$@\"\n",
        "gh": (
            "#!/bin/sh\n"
            "if [ \"$1\" != api ] || [ \"$2\" != --paginate ] || "
            "[ \"$3\" != repos/fixture/check/pulls/1/commits ]; then exit 91; fi\n"
            "printf '%s\\n' \"$FIXTURE_COMMITS\"\n"
        ),
    }
    for name, content in wrappers.items():
        path = bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
    head = _git(checkout, "rev-parse", "HEAD").strip()
    needs = {"tests": {"result": result}}
    if result == "missing":
        needs = {"detect": {"result": "success", "outputs": {"python": "true"}}}
    return {
        "PATH": str(bin_dir) + os.pathsep + os.defpath,
        "HOME": str(tmp_path), "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GH_TOKEN": "", "GITHUB_TOKEN": "",
        "GITHUB_REPOSITORY": "fixture/check", "PR": "1", "HEAD_SHA": head,
        "GITHUB_OUTPUT": str(tmp_path / "github-output.txt"),
        "RELEASE": "false", "NEEDS": json.dumps(needs),
        "FIXTURE_COMMITS": json.dumps({"sha": head, "parents": [], "email": _EMAIL, "author": "CI Fixture"}),
    }


_CASES = [
    ("aggregate", "success", 0), ("aggregate", "failure", 1),
    ("aggregate", "cancelled", 1), ("aggregate", "missing", 1),
    *[(kind, result, int(result != "success"))
      for kind in ("contributor", "history", "infographic", "case")
      for result in ("success", "reject")],
]


@pytest.mark.parametrize(
    ("kind", "result", "expected"),
    [pytest.param(kind, result, expected, id=f"{kind}-{result}") for kind, result, expected in _CASES],
)
def test_metadata_checkout_keeps_policy_coverage_without_application_payloads(tmp_path, kind, result, expected):
    filename, job_id, step_name = _JOBS[kind]
    workflow = YAML(typ="base").load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))
    steps = workflow["jobs"][job_id]["steps"]
    selection = next(step["with"] for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    checkout = _checkout(tmp_path, selection, kind, result)
    body = next(step["run"] for step in steps if step.get("name") == step_name)
    cp = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", body],
        cwd=checkout, env=_ports(tmp_path, checkout, result), capture_output=True, text=True, timeout=30, check=False,
    )
    assert cp.returncode == expected, cp.stdout + cp.stderr
    if expected:
        assert "::error::" in cp.stdout or "::error::" in cp.stderr or "without a mapping" in cp.stdout
    assert not (checkout / "assets/unneeded.bin").exists()


@pytest.mark.parametrize("repository", ["Coding-Sensations/hermes-agent", "NousResearch/hermes-agent"])
def test_declared_python_slice_policy_keeps_every_file_once(repository, tmp_path, capsys):
    from scripts import run_tests_parallel as runner

    workflow = YAML(typ="base").load((ROOT / ".github/workflows/ci.yaml").read_text(encoding="utf-8"))
    count = int(gha.render(workflow["jobs"]["tests"]["with"]["slice_count"], {"github": {"repository": repository}}))
    assert count > 0
    files = [tmp_path / f"tests/test_{index:03}.py" for index in range(101)]
    caches = [{f"tests/test_{index:03}.py": float(index) for index in range(101)}, {}, {"tests/test_000.py": 500.0}]
    selected = [
        runner._slice_files(list(reversed(files)) if index % 2 else files, index + 1, count, caches[index % 3], tmp_path)
        for index in range(count)
    ]
    assert Counter(path for bucket in selected for path in bucket) == Counter(files)
