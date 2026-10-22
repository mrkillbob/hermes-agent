"""Tests for scripts/verify-bootstrap-version-stamp.py.

The bootstrap installers stamp ``.hermes-bootstrap-complete`` with the
commit/branch they pinned; this script reads the stamp back and cross-checks
it against the installed checkout. Tests build a real temp git repo, write
stamps by hand, and verify the honest and lying cases.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _git_exe() -> str:
    """A git CreateProcess can start. conftest blanks SystemRoot/ComSpec and
    this host's PATH can resolve `git` to the MSIX payload copy under
    ``C:\\Program Files\\WindowsApps\\...`` — WinError 5 outside its package
    context. Prefer a conventional install."""
    candidates = [hit for hit in (shutil.which("git"),) if hit]
    if sys.platform == "win32":
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git"
        for rel in (("cmd", "git.exe"), ("bin", "git.exe")):
            p = base.joinpath(*rel)
            if p.exists():
                candidates.append(str(p))
    for cand in candidates:
        if "windowsapps" not in cand.lower():
            return cand
    return candidates[0]


_GIT = _git_exe()


def _git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    if sys.platform == "win32":
        env.setdefault("SystemRoot", r"C:\Windows")
        env.setdefault("ComSpec", r"C:\Windows\system32\cmd.exe")
    out = subprocess.run(
        [_GIT, *args], cwd=repo, capture_output=True, text=True, check=True, env=env
    )
    return out.stdout.strip()

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify-bootstrap-version-stamp.py"


def _verify(repo: Path, path: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if os.name == "nt":
        env.setdefault("SystemRoot", r"C:\Windows")
        env.setdefault("ComSpec", r"C:\Windows\system32\cmd.exe")
    return subprocess.run([sys.executable, str(_SCRIPT), "--repo", str(repo), "--stamp", str(path), *args],
                          env=env, capture_output=True, text=True, timeout=30, check=False)


def _install_repo(tmp_path: Path) -> Path:
    """A minimal 'installed checkout': git repo + the source identity stamp."""
    repo = tmp_path / "install"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "ci@example.com")
    _git(repo, "config", "user.name", "ci")
    (repo / "hermes_cli").mkdir()
    (repo / "hermes_cli" / "__init__.py").write_text(
        '"""Hermes CLI."""\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    (repo / "install-stamp.json").write_text(json.dumps({
        "schemaVersion": 2, "commit": _git(repo, "rev-parse", "HEAD"),
        "baseVersion": "0.1.2", "updateMechanism": "self",
    }), encoding="utf-8")
    return repo


@pytest.mark.parametrize("changes,expect,error", [
    ({}, [], None),
    ({"pinnedCommit": "a" * 40}, [], "installed HEAD"),
    ({}, ["--expect-commit", "b" * 40], "expected"),
    ({}, ["--expect-branch", "release"], "pinnedBranch"),
    ({"schemaVersion": 2}, [], "schemaVersion"),
    ({"pinnedCommit": "deadbeef"}, [], "40-char"),
    ({"completedAt": "not-a-time"}, [], "ISO-8601"),
    ({"completedAt": "2026-08-30T12:00:00+01:00"}, [], "not UTC"),
    ({"missing": "stamp"}, [], "cannot read stamp"),
    ({"missing": "version"}, [], "no source identity stamp"),
    ({"canonicalCommit": "c" * 40}, [], "canonical"),
    # A checkout with no reachable release tag honestly stamps a null base.
    ({"canonicalBase": None}, [], None),
    # The protocol lane never runs the products stage that writes the stamp.
    ({"missing": "version"}, ["--no-source-stamp"], None),
])
def test_verifier_cli(tmp_path, changes, expect, error):
    repo = _install_repo(tmp_path)
    stamp = {"schemaVersion": 1, "pinnedCommit": _git(repo, "rev-parse", "HEAD"),
             "pinnedBranch": "main", "completedAt": "2026-08-30T12:00:00.000Z", **changes}
    path = repo / ".hermes-bootstrap-complete"
    if changes.get("missing") != "stamp":
        path.write_text(json.dumps(stamp), encoding="utf-8")
    if changes.get("missing") == "version":
        (repo / "install-stamp.json").unlink()
    if "canonicalCommit" in changes or "canonicalBase" in changes:
        canonical = json.loads((repo / "install-stamp.json").read_text(encoding="utf-8"))
        canonical["commit"] = changes.get("canonicalCommit", canonical["commit"])
        canonical["baseVersion"] = changes.get("canonicalBase", canonical["baseVersion"])
        (repo / "install-stamp.json").write_text(json.dumps(canonical), encoding="utf-8")
    if not error:
        expect = ["--expect-commit", stamp["pinnedCommit"], "--expect-branch", "main", *expect]
    result = _verify(repo, path, *expect)
    assert result.returncode == (1 if error else 0), result.stderr
    assert error in result.stderr if error else "stamp verified" in result.stdout


@pytest.mark.parametrize("broken_identity", [None, "head", "source", "missing-source", "malformed-source"])
def test_pre_receipt_upgrade_still_requires_checkout_identity(tmp_path, broken_identity):
    repo = _install_repo(tmp_path)
    scripts = repo / "scripts"
    scripts.mkdir()
    installer = scripts / "install.ps1"
    installer.write_text('Write-Host "Legacy install complete"\n', encoding="utf-8")
    _git(repo, "add", "scripts/install.ps1")
    _git(repo, "commit", "-m", "pre-receipt installer")
    initial = _git(repo, "rev-parse", "HEAD")
    installer.write_text('Write-Host ".hermes-bootstrap-complete"\n', encoding="utf-8")
    _git(repo, "commit", "-am", "current installer")
    head = _git(repo, "rev-parse", "HEAD")
    source = repo / "install-stamp.json"
    source.write_text(json.dumps({"commit": "a" * 40 if broken_identity == "source" else head}), encoding="utf-8")
    if broken_identity == "missing-source":
        source.unlink()
    elif broken_identity == "malformed-source":
        source.write_text("invalid JSON", encoding="utf-8")
    result = _verify(repo, repo / ".hermes-bootstrap-complete",
        "--initial-windows-installer", initial,
        "--expect-commit", "b" * 40 if broken_identity == "head" else head,
    )
    assert result.returncode == (1 if broken_identity else 0), result.stderr
    if broken_identity:
        assert "FAIL:" in result.stderr
    else:
        assert "stamp verified" in result.stdout


@pytest.mark.parametrize("case", [
    "receipt-capable", "unknown-initial", "unrelated-initial", "branch-initial", "abbreviated-initial",
    "invalid-receipt", "stale-receipt", "schema-receipt", "timestamp-receipt",
])
def test_legacy_mode_never_excuses_required_or_invalid_receipts(tmp_path, case):
    repo = _install_repo(tmp_path)
    scripts = repo / "scripts"
    scripts.mkdir()
    capability = ".hermes-bootstrap-complete" if case == "receipt-capable" else "Legacy install complete"
    (scripts / "install.ps1").write_text(f'Write-Host "{capability}"\n', encoding="utf-8")
    _git(repo, "add", "scripts/install.ps1")
    _git(repo, "commit", "-m", "installer")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / "install-stamp.json").write_text(json.dumps({"commit": head}), encoding="utf-8")
    receipt = repo / ".hermes-bootstrap-complete"
    if case == "invalid-receipt":
        receipt.write_text("invalid JSON", encoding="utf-8")
    elif case in {"stale-receipt", "schema-receipt", "timestamp-receipt"}:
        content = {"schemaVersion": 1, "pinnedCommit": head, "pinnedBranch": "main",
                   "completedAt": "2026-08-30T12:00:00.000Z"}
        key, value = {"stale-receipt": ("pinnedCommit", "a" * 40), "schema-receipt": ("schemaVersion", 2),
                      "timestamp-receipt": ("completedAt", "not-a-time")}[case]
        content[key] = value
        receipt.write_text(json.dumps(content), encoding="utf-8")
    initial = head
    if case == "unknown-initial":
        initial = "c" * 40
    elif case == "unrelated-initial":
        initial = _git(repo, "commit-tree", _git(repo, "rev-parse", "HEAD^{tree}"), "-m", "unrelated root")
    elif case == "branch-initial":
        initial = "main"
    elif case == "abbreviated-initial":
        initial = head[:12]
    result = _verify(repo, receipt,
        "--initial-windows-installer", initial,
        "--expect-commit", head,
    )
    assert result.returncode == 1, result.stderr
    assert "FAIL:" in result.stderr
