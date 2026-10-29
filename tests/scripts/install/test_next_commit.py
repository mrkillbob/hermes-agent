"""Synthetic NEXT must exercise a Desktop build, while no-op updates remain reusable (#443)."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
DESKTOP_INPUT = "apps/desktop/electron/app-version.ts"


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *arguments],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "source with spaces"
    for name in ("scripts/build/freshness.mjs", "scripts/build/frontend-common.mjs"):
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY / name, destination)
    source = root / DESKTOP_INPUT
    source.parent.mkdir(parents=True)
    source.write_text("export const version = 'fixture'\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "installed source")
    return root


def _desktop_hash(root: Path) -> str:
    node = shutil.which("node")
    assert node, "the production Desktop freshness scanner requires Node"
    return subprocess.run(
        [node, "--input-type=module", "-e",
         "import {sourceHash} from './scripts/build/freshness.mjs'; console.log(sourceHash('.', 'desktop'))"],
        cwd=root, capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()


def _assert_desktop_update(root: Path, parent: str, target: str, before: str, index: bytes, refs: str):
    assert _git(root, "rev-parse", f"{target}^") == parent
    assert _git(root, "rev-parse", "HEAD") == parent
    assert (root / ".git/index").read_bytes() == index
    assert _git(root, "show-ref") == refs
    assert _git(root, "status", "--porcelain") == ""
    _git(root, "checkout", "--detach", target)
    assert (root / ".hermes-e2e-next").is_file()
    assert _desktop_hash(root) != before, "NEXT must change a real Desktop build input, not only a root marker"
    assert _git(root, "diff", parent, "--", DESKTOP_INPUT), "the strict NEXT smoke needs a genuine Desktop-input update"


@pytest.mark.platforms("posix")
def test_posix_next_changes_desktop_inputs_without_moving_checkout_or_index(checkout, tmp_path):
    root = checkout
    parent = _git(root, "rev-parse", "HEAD")
    before, index, refs = _desktop_hash(root), (root / ".git/index").read_bytes(), _git(root, "show-ref")
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", 'source "$ASSET"; resolve_update_ref "$REPO" "$PARENT" NEXT'],
        env=dict(os.environ, ASSET=str(REPOSITORY / "tests/install/e2e-assets/installer-common.sh"),
                 REPO=str(root), PARENT=parent, TMPDIR=str(tmp_path)),
        capture_output=True, text=True, check=True, timeout=30,
    )
    _assert_desktop_update(root, parent, result.stdout.strip(), before, index, refs)


@pytest.mark.platforms("windows")
def test_powershell_next_changes_desktop_inputs_without_moving_checkout_or_index(checkout, tmp_path):
    root = checkout
    parent = _git(root, "rev-parse", "HEAD")
    before, index, refs = _desktop_hash(root), (root / ".git/index").read_bytes(), _git(root, "show-ref")
    shell = shutil.which("powershell") or shutil.which("pwsh")
    assert shell, "native Windows acceptance requires PowerShell"
    script = tmp_path / "probe.ps1"
    script.write_text('''$ErrorActionPreference = 'Stop'
$WorkRoot = $env:WORK
function Invoke-Git([string[]]$GitArgs) {
    $output = & git @GitArgs
    if ($LASTEXITCODE -ne 0) { throw "git failed: $GitArgs" }
    return ($output | Out-String).Trim()
}
. $env:ASSET
New-NextCommit $env:REPO $env:PARENT
''', encoding="utf-8")
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=dict(os.environ, ASSET=str(REPOSITORY / "tests/install/e2e-assets/next-commit.ps1"),
                 REPO=str(root), PARENT=parent, WORK=str(tmp_path)),
        capture_output=True, text=True, check=True, timeout=30,
    )
    _assert_desktop_update(root, parent, result.stdout.strip(), before, index, refs)


def test_root_only_commit_preserves_desktop_input_identity(checkout):
    root = checkout
    before = _desktop_hash(root)
    (root / ".hermes-e2e-next").write_text("a source-only update\n", encoding="utf-8")
    _git(root, "add", ".hermes-e2e-next")
    _git(root, "commit", "-qm", "no Desktop change")
    assert _desktop_hash(root) == before, "a root marker must not disable legitimate Desktop reuse"
