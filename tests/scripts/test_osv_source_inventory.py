"""A source inventory exports canonical stamp identity without changing package matching."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    from scripts.write_install_stamp import build_stamp

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("HERMES_BUILD_COMMIT", "HERMES_DESKTOP_VARIANT", "HERMES_PAYLOAD_TAG"):
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "hermes-agent"\nversion = "0.0.0"\n', encoding="utf-8")
    (source / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "hermes-agent"\nversion = "0.0.0"\n'
        'source = { editable = "." }\n\n[[package]]\nname = "example-dependency"\n'
        'version = "1.2.3"\nsource = { registry = "https://pypi.org/simple" }\n', encoding="utf-8")

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "commit.gpgsign=false", *args], cwd=source,
            text=True, encoding="utf-8", timeout=10).strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("add", ".")
    git("commit", "-qm", "fixture")
    stamp = build_stamp(
        commit=git("rev-parse", "HEAD"), branch="main", dirty=False, commit_date=1,
        source="ci", update_mechanism="external")
    return source, stamp, git


def export_cli(checkout, tmp_path, *, output=None):
    source, stamp, _ = checkout
    stamp_path = tmp_path / "install-stamp.json"
    stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
    output = output or tmp_path / "source-inventory.osv.json"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.build.osv_source_inventory", "--source", str(source),
         "--stamp", str(stamp_path), "--repository", "example/hermes-fork", "--output", str(output)],
        cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True,
        text=True, encoding="utf-8", timeout=10, check=False)
    return result, output, stamp_path


def test_exports_existing_stamp_commit_without_rewriting_package_or_release_identity(checkout, tmp_path):
    source, stamp, _ = checkout
    before = {name: (source / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    result, output, stamp_path = export_cli(checkout, tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text()) == {"results": [{"packages": [{"package": {
        "name": "https://github.com/example/hermes-fork", "commit": stamp["commit"]}}]}]}
    assert json.loads(stamp_path.read_text()) == stamp
    assert {name: (source / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("change", [
    {"commit": "0" * 40}, {"commit": "a" * 40}, {"commit": "short"},
    {"dirty": True}, {"dirty": None}, {"source": "fallback"}, {"schemaVersion": -1},
])
def test_rejects_unqualified_or_mismatched_stamp(checkout, tmp_path, change):
    checkout[1].update(change)
    result, output, _ = export_cli(checkout, tmp_path)
    assert result.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize("tracked", [True, False])
def test_rejects_dirty_source(checkout, tmp_path, tracked):
    source, _, _ = checkout
    (source / ("uv.lock" if tracked else "untracked.txt")).write_text("changed\n", encoding="utf-8")
    result, output, _ = export_cli(checkout, tmp_path)
    assert result.returncode != 0 and "clean source checkout" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("root", [
    'name = "other"\nversion = "0.0.0"\nsource = { editable = "." }',
    'name = "hermes-agent"\nversion = "9.9.9"\nsource = { editable = "." }',
    'name = "hermes-agent"\nversion = "0.0.0"\nsource = { registry = "https://pypi.org/simple" }',
    'name = "hermes-agent"\nversion = "0.0.0"\nsource = { editable = "." }\n'
    '[[package]]\nname = "duplicate"\nversion = "0.0.0"\nsource = { editable = "." }',
])
def test_binds_exactly_one_editable_root_to_project(checkout, tmp_path, root):
    source, stamp, git = checkout
    (source / "uv.lock").write_text("version = 1\n[[package]]\n" + root + "\n", encoding="utf-8")
    git("add", "uv.lock")
    git("commit", "-qm", "different lock root")
    stamp["commit"] = git("rev-parse", "HEAD")
    result, output, _ = export_cli(checkout, tmp_path)
    assert result.returncode != 0 and "editable root" in result.stderr
    assert not output.exists()


def test_refuses_output_in_source_or_overwriting_existing_artifact(checkout, tmp_path):
    source, _, _ = checkout
    result, output, _ = export_cli(checkout, tmp_path, output=source / "osv-scanner-custom.json")
    assert result.returncode != 0 and "outside" in result.stderr
    assert not output.exists()
    existing = tmp_path / "existing.json"
    existing.write_text("retain\n", encoding="utf-8")
    result, _, _ = export_cli(checkout, tmp_path, output=existing)
    assert result.returncode != 0
    assert existing.read_text() == "retain\n"


@pytest.mark.parametrize("repository,valid", [
    ("a/b", True), ("example/.github", True),
    ("https://github.com/example/fork", False), ("example/fork?token=synthetic", False),
    ("example/..", False),
])
def test_repository_label_accepts_short_names_but_not_url_parameters(checkout, repository, valid):
    from scripts.build.osv_source_inventory import source_inventory

    source, stamp, _ = checkout
    if valid:
        package = source_inventory(source, stamp, repository)["results"][0]["packages"][0]["package"]
        assert package["name"] == "https://github.com/" + repository
    else:
        with pytest.raises(ValueError, match="owner/repository"):
            source_inventory(source, stamp, repository)


@pytest.mark.parametrize("field,value", [
    ("updateMechanism", None), ("updateMechanism", "carrier-pigeon"),
    ("source", 17), ("source", []),
])
def test_enforces_canonical_stamp_contract_before_export(checkout, tmp_path, field, value):
    stamp = checkout[1]
    if value is None:
        stamp.pop(field)
    else:
        stamp[field] = value
    result, output, _ = export_cli(checkout, tmp_path)
    assert result.returncode == 2 and "qualified clean install stamp" in result.stderr
    assert not output.exists()


def test_output_boundary_uses_actual_git_checkout_not_nested_project(checkout, tmp_path):
    source, stamp, git = checkout
    nested = source / "component"
    nested.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        (nested / name).write_bytes((source / name).read_bytes())
    git("add", "component")
    git("commit", "-qm", "nested project")
    stamp["commit"] = git("rev-parse", "HEAD")
    result, output, _ = export_cli((nested, stamp, git), tmp_path,
                                  output=source / "osv-scanner-custom.json")
    assert result.returncode != 0 and "Git checkout root" in result.stderr
    assert not output.exists()
