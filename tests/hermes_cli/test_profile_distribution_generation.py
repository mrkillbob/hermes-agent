"""An update must not overwrite a distribution replaced while its source stages."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, UTC
import subprocess
import threading

import pytest

from hermes_cli import profile_distribution as dist


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("replacement", ["other-source", "same-source", "same-fields"])
def test_update_refuses_in_place_force_install_after_staging(tmp_path, monkeypatch, replacement):
    monkeypatch.setattr(dist, "datetime", FixedClock)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    source = tmp_path / "source-a"
    source.mkdir()
    (source / "SOUL.md").write_text("original payload\n")
    dist.write_manifest(source, dist.DistributionManifest(name="shared", version="1.0.0"))
    source_arg = str(source)

    def commit():
        subprocess.run(["git", "-C", str(source), "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "payload"], capture_output=True, check=True)

    if replacement == "same-fields":
        subprocess.run(["git", "init", "-q", str(source)], capture_output=True, check=True)
        commit()
        source_arg = "https://example.invalid/distribution.git"

        def clone_local(url, destination):
            assert url == source_arg
            subprocess.run(["git", "clone", "-q", str(source), str(destination)], capture_output=True, check=True)

        monkeypatch.setattr(dist, "_git_clone", clone_local)
    target = dist.install_distribution(source_arg).target_dir
    identity = target.stat()
    rival = source if replacement != "other-source" else tmp_path / "source-b"
    rival.mkdir(exist_ok=True)
    staged, published, release, reserving = (threading.Event() for _ in range(4))
    original_plan, original_copy = dist.plan_install, dist._copy_dist_payload
    original_reservation = dist.profile_lifecycle_reservation
    replacement_bytes = {}
    role = threading.local()

    def plan(*args, **kwargs):
        result = original_plan(*args, **kwargs)
        if role.value == "update":
            staged.set()
            assert published.wait(5)
        return result

    def copy(*args, **kwargs):
        original_copy(*args, **kwargs)
        if role.value == "replace":
            replacement_bytes.update({p.name: p.read_bytes() for p in target.iterdir() if p.is_file()})
            published.set()
            assert release.wait(5)

    @contextmanager
    def reserve(*homes):
        if role.value == "update":
            reserving.set()
        with original_reservation(*homes):
            yield

    def replace():
        role.value = "replace"
        assert staged.wait(5)
        (rival / "SOUL.md").write_text("replacement payload\n")
        dist.write_manifest(rival, dist.DistributionManifest(
            name="shared", version="1.0.0" if replacement == "same-fields" else "2.0.0"))
        if replacement == "same-fields":
            commit()
        return dist.install_distribution(source_arg if replacement == "same-fields" else str(rival), force=True)

    def update():
        role.value = "update"
        return dist.update_distribution("shared")

    monkeypatch.setattr(dist, "plan_install", plan)
    monkeypatch.setattr(dist, "_copy_dist_payload", copy)
    monkeypatch.setattr(dist, "profile_lifecycle_reservation", reserve)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="distribution-race") as pool:
        installed = pool.submit(replace)
        updated = pool.submit(update)
        try:
            assert reserving.wait(5), "update never reached the held lifecycle reservation"
        finally:
            release.set()
        assert installed.result(timeout=5).target_dir == target
        with pytest.raises(dist.DistributionError, match="changed while staging"):
            updated.result(timeout=5)

    assert (target.stat().st_dev, target.stat().st_ino) == (identity.st_dev, identity.st_ino)
    assert {p.name: p.read_bytes() for p in target.iterdir() if p.is_file()} == replacement_bytes
    manifest = dist.read_manifest(target)
    assert manifest.source == (source_arg if replacement == "same-fields" else str(rival.resolve()))
    assert manifest.version == ("1.0.0" if replacement == "same-fields" else "2.0.0")
    assert (target / "SOUL.md").read_text() == "replacement payload\n"


def test_publications_replace_source_generation_and_preserve_update_state(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(dist, "datetime", FixedClock)
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.yaml").write_text("model: source\n")
    dist.write_manifest(source, dist.DistributionManifest(name="published"))
    manifest_path = source / dist.MANIFEST_FILENAME
    manifest_path.write_text(manifest_path.read_text() + "installed_generation: source-controlled\n")
    target = dist.install_distribution(str(source)).target_dir
    first = dist.read_manifest(target).to_dict().get("installed_generation")
    assert first and first != "source-controlled"
    (target / "config.yaml").write_text("model: user\n")
    (target / "memories/local.txt").write_text("retained\n")
    # Even shipping the previous installed marker cannot preserve publication identity.
    manifest_path.write_text(manifest_path.read_text().replace("source-controlled", first))
    second = dist.update_distribution("published").manifest.to_dict()["installed_generation"]
    assert second and second != first
    assert (target / "config.yaml").read_text() == "model: user\n"
    assert (target / "memories/local.txt").read_text() == "retained\n"
    third = dist.install_distribution(str(source), force=True).manifest.to_dict()["installed_generation"]
    assert third not in {first, second}
    assert (target / "memories/local.txt").read_text() == "retained\n"


def test_legacy_manifest_without_generation_updates_normally(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    source = tmp_path / "legacy-source"
    source.mkdir()
    dist.write_manifest(source, dist.DistributionManifest(name="legacy"))
    target = dist.install_distribution(str(source)).target_dir
    path = target / dist.MANIFEST_FILENAME
    data = dist.yaml.safe_load(path.read_text())
    data.pop("installed_generation", None)
    path.write_text(dist.yaml.safe_dump(data))
    updated = dist.update_distribution("legacy")
    assert updated.manifest.to_dict().get("installed_generation")
