"""Scan evidence binds the admitted checkout without altering package inventory."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.scripts import test_osv_source_inventory as inventory_fixtures

checkout = inventory_fixtures.checkout

ROOT = Path(__file__).resolve().parents[2]


def collect_cli(checkout, tmp_path, *, output=None, repository="example/hermes-fork"):
    source, _, _ = checkout
    output = output or tmp_path / "scan-identity"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.build.osv_scan_identity",
         "--source", str(source), "--repository", repository, "--output", str(output)],
        cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=10, check=False)
    return result, output


def test_collects_actual_checkout_stamp_and_commit_inventory_without_package_changes(checkout, tmp_path, monkeypatch):
    source, _, git = checkout
    commit = git("rev-parse", "HEAD")
    before = {name: (source / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    result, output = collect_cli(checkout, tmp_path)
    assert result.returncode == 0, result.stderr
    stamp = json.loads((output / "install-stamp.json").read_text())
    inventory = json.loads((output / "source-inventory.osv.json").read_text())
    assert stamp["commit"] == commit
    assert stamp["source"] == "ci" and stamp["dirty"] is False
    assert stamp["updateMechanism"] == "external"
    assert inventory == {"results": [{"packages": [{"package": {
        "name": "https://github.com/example/hermes-fork", "commit": commit}}]}]}
    assert {name: (source / name).read_bytes() for name in before} == before
    assert git("status", "--porcelain", "--untracked-files=all") == ""


@pytest.mark.parametrize("tracked", [True, False])
def test_rejects_dirty_scan_checkout_before_writing_identity(checkout, tmp_path, tracked):
    source, _, _ = checkout
    (source / ("uv.lock" if tracked else "untracked.txt")).write_text("changed\n")
    result, output = collect_cli(checkout, tmp_path)
    assert result.returncode == 2 and "clean source checkout" in result.stderr
    assert not output.exists()


def test_rejects_output_inside_checkout_without_creating_scan_input(checkout, tmp_path):
    source, _, _ = checkout
    output = source / "scan-identity"
    result, _ = collect_cli(checkout, tmp_path, output=output)
    assert result.returncode == 2 and "outside" in result.stderr
    assert not output.exists()


def test_refuses_existing_identity_directory_without_overwriting_receipts(checkout, tmp_path):
    output = tmp_path / "identity"
    output.mkdir()
    marker = output / "install-stamp.json"
    marker.write_bytes(b"retain existing identity\n")
    result, _ = collect_cli(checkout, tmp_path, output=output)
    assert result.returncode == 2
    assert marker.read_bytes() == b"retain existing identity\n"
    assert not (output / "source-inventory.osv.json").exists()


def test_invalid_repository_does_not_leave_a_plausible_partial_identity(checkout, tmp_path):
    result, output = collect_cli(checkout, tmp_path, repository="https://github.com/example/fork?token=synthetic")
    assert result.returncode == 2 and "owner/repository" in result.stderr
    assert not output.exists()
