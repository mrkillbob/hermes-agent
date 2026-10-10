"""Regression for #222: fork tags cannot hide a proven upstream release ancestor."""
import subprocess

from hermes_cli import version_info
from hermes_cli.plugins_manifest import running_hermes_version, version_satisfies


def test_source_release_ancestry_preserves_plugin_compatibility(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True,
            check=True, timeout=10,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Hermes Test")
    git("config", "user.email", "hermes@example.invalid")
    project = repo / "pyproject.toml"
    project.write_text('[project]\nversion = "0.20.4"\n', encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "old fork release")
    old = git("rev-parse", "HEAD")
    git("tag", "v2026.8.18")
    git("tag", "v0.20.4")
    project.write_text('[project]\nversion = "0.21.5"\n', encoding="utf-8")
    git("commit", "-qam", "upstream release")
    anchor = git("rev-parse", "HEAD")
    project.write_text('[project]\nversion = "0.0.0"\n', encoding="utf-8")
    git("commit", "-qam", "development version placeholder")
    tip = git("rev-parse", "HEAD")
    monkeypatch.setattr(version_info, "_UPSTREAM_CALVER_RELEASE", ("0.21.5", anchor), raising=False)
    monkeypatch.setattr(version_info, "_resolve_repo_dir", lambda: repo)
    monkeypatch.setattr(version_info, "_resolve_stamp_file", lambda: None)

    def identity():
        version_info._reset_version_info_cache()
        return version_info.get_version_info()

    info = identity()
    assert info.base_version == "0.21.5"
    assert info.distance == 1
    assert info.commit == tip
    assert running_hermes_version() == "0.21.5"
    assert version_satisfies(">=0.21.5", running_hermes_version())
    assert not version_satisfies(">=0.21.6", running_hermes_version())
    git("checkout", "-q", old)
    assert identity().base_version == "0.20.4"
    assert not version_satisfies(">=0.21.5", running_hermes_version())
    git("checkout", "-q", tip)
    git("tag", "v0.21.6")
    assert identity().base_version == "0.21.6"
    assert identity().distance == 0


def test_unproven_source_release_does_not_raise_compatibility_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(version_info, "_UPSTREAM_CALVER_RELEASE", ("0.21.5", "a" * 40), raising=False)
    monkeypatch.setattr(version_info, "_run_git", lambda *args: None)
    assert version_info._upstream_calver_release(tmp_path) is None
