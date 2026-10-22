"""An update must not overwrite a distribution replaced while its source stages."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import threading

import pytest

from hermes_cli import profile_distribution as dist


@pytest.mark.parametrize("replacement", ["other-source", "same-source"])
def test_update_refuses_in_place_force_install_after_staging(tmp_path, monkeypatch, replacement):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    source = tmp_path / "source-a"
    source.mkdir()
    (source / "SOUL.md").write_text("original payload\n")
    dist.write_manifest(source, dist.DistributionManifest(name="shared", version="1.0.0"))
    target = dist.install_distribution(str(source)).target_dir
    identity = target.stat()
    rival = source if replacement == "same-source" else tmp_path / "source-b"
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
        dist.write_manifest(rival, dist.DistributionManifest(name="shared", version="2.0.0"))
        return dist.install_distribution(str(rival), force=True)

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
    assert manifest.source == str(rival.resolve())
    assert manifest.version == "2.0.0"
    assert (target / "SOUL.md").read_text() == "replacement payload\n"
