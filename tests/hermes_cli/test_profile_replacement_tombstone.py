"""Successful explicit profile replacement must retire its old deletion marker."""

from pathlib import Path
import tarfile

import pytest

from hermes_cli import profile_distribution, profiles
from hermes_constants import mark_named_profile_deleted, named_profile_is_deleted


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _replacement(tmp_path, kind):
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.yaml").write_text("model: replacement\n", encoding="utf-8")
    if kind == "distribution":
        profile_distribution.write_manifest(
            source, profile_distribution.DistributionManifest(name="original", version="0.1.0")
        )
        return lambda: profile_distribution.install_distribution(
            str(source), name="replaced"
        ).target_dir
    archive = tmp_path / "profile.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(source, arcname="original")
    return lambda: profiles.import_profile(str(archive), name="replaced")


@pytest.mark.parametrize("kind", ["distribution", "import"])
def test_successful_replacement_of_deleted_name_is_visible(profile_env, tmp_path, monkeypatch, kind):
    target = profiles.get_profile_dir("replaced")
    mark_named_profile_deleted(target)
    replace = _replacement(tmp_path, kind)
    if kind == "distribution":
        copy_payload = profile_distribution._copy_dist_payload

        def copy_while_unpublished(*args, **kwargs):
            copy_payload(*args, **kwargs)
            assert named_profile_is_deleted(target)
            assert not profiles.profile_exists("replaced")

        monkeypatch.setattr(profile_distribution, "_copy_dist_payload", copy_while_unpublished)

    assert replace() == target

    assert (target / "config.yaml").read_text(encoding="utf-8") == "model: replacement\n"
    assert not named_profile_is_deleted(target)
    assert profiles.profile_exists("replaced")
    assert "replaced" in profiles.list_profile_names()
    assert profiles.resolve_profile_env("replaced") == str(target)


@pytest.mark.parametrize("kind", ["distribution", "import"])
def test_failed_replacement_keeps_deletion_marker(profile_env, tmp_path, monkeypatch, kind):
    target = profiles.get_profile_dir("replaced")
    mark_named_profile_deleted(target)
    replace = _replacement(tmp_path, kind)

    def fail_publication(*args, **kwargs):
        raise OSError("synthetic replacement failure")

    if kind == "distribution":
        monkeypatch.setattr(profile_distribution, "_copy_dist_payload", fail_publication)
    else:
        monkeypatch.setattr(profiles.shutil, "move", fail_publication)
    with pytest.raises(OSError, match="synthetic replacement failure"):
        replace()

    assert named_profile_is_deleted(target)
    assert not profiles.profile_exists("replaced")
    assert "replaced" not in profiles.list_profile_names()
