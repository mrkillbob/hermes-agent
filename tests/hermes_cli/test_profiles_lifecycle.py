"""Real lifecycle consumers reserve names against concurrent project adoption."""
import subprocess
import sys

import pytest

from hermes_cli import profiles

pytestmark = pytest.mark.platforms("posix")


def assert_reserved(home):
    from hermes_cli.profiles_lifecycle import _lock_path
    probe = "import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
    result = subprocess.run([sys.executable, "-c", probe, str(_lock_path(home))], capture_output=True, check=False)
    assert result.returncode != 0 and b"BlockingIOError" in result.stderr


@pytest.mark.parametrize("operation", ["delete", "rename", "recreate"])
def test_actual_lifecycle_holds_reservation_before_its_side_effects(tmp_path, monkeypatch, operation):
    root = tmp_path.resolve() / "root"
    home = root / "profiles" / "old"
    home.mkdir(parents=True)
    (home / "profile.yaml").write_text("name: old\n")
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: root)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: root / "profiles")
    seen = []
    class Reached(Exception):
        pass
    def guarded(*args, **kwargs):
        assert_reserved(home)
        if operation == "rename":
            assert_reserved(root / "profiles" / "new")
        seen.append(True)
        raise Reached
    if operation == "recreate":
        (home / "profile.yaml").unlink()
        profiles.mark_named_profile_deleted(home)
        monkeypatch.setattr(profiles, "wait_profile_unowned", lambda *a, **k: True)
        monkeypatch.setattr(profiles, "clear_named_profile_deleted", guarded)
    else:
        monkeypatch.setattr(profiles, "_check_gateway_running", guarded)
    with pytest.raises(Reached):
        if operation == "delete":
            profiles.delete_profile("old", yes=True)
        elif operation == "rename":
            profiles.rename_profile("old", "new")
        else:
            profiles.create_profile("old", no_alias=True)
    assert seen == [True]


def test_reservation_is_reentrant_and_does_not_reserve_unrelated_profile(tmp_path):
    from hermes_cli.profiles_lifecycle import profile_lifecycle_reservation, _lock_path
    home = tmp_path / "profiles" / "first"
    other = tmp_path / "profiles" / "other"
    with profile_lifecycle_reservation(home), profile_lifecycle_reservation(home):
        assert_reserved(home)
        other_lock = _lock_path(other)
        other_lock.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([sys.executable, "-c",
            "import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)",
            str(other_lock)], capture_output=True, check=False)
        assert result.returncode == 0


@pytest.mark.parametrize("explicit_name", [None, "renamed"])
def test_import_reserves_resolved_name_before_collision_check_and_publication(tmp_path, monkeypatch, explicit_name):
    import tarfile
    root = tmp_path / "root"
    source = tmp_path / "source"
    source.mkdir()
    (source / "profile.yaml").write_text("name: archived\n")
    archive = tmp_path / "profile.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="archived")
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: root / "profiles")
    home = root / "profiles" / (explicit_name or "archived")
    original_exists = type(home).exists
    original_move = profiles.shutil.move
    phases = []
    def exists(path):
        if path == home:
            assert_reserved(home)
            phases.append("checked")
        return original_exists(path)
    def move(src, dst, *args, **kwargs):
        if str(dst) == str(home):
            assert_reserved(home)
            phases.append("published")
        return original_move(src, dst, *args, **kwargs)
    monkeypatch.setattr(type(home), "exists", exists)
    monkeypatch.setattr(profiles.shutil, "move", move)
    assert profiles.import_profile(str(archive), explicit_name) == home
    assert phases[0] == "checked" and "published" in phases
    with pytest.raises(FileExistsError):
        profiles.import_profile(str(archive), explicit_name)


@pytest.mark.parametrize("force", [False, True])
def test_distribution_install_rechecks_collision_after_staging_under_reservation(tmp_path, monkeypatch, force):
    from hermes_cli import profile_distribution as dist
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    source = tmp_path / "distribution"
    source.mkdir()
    dist.write_manifest(source, dist.DistributionManifest(name="installed", version="0.1.0"))
    original_plan = dist.plan_install
    home = profiles.get_profile_dir("installed")
    def staged(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        # A competing publisher completed while source staging was in progress.
        home.mkdir(parents=True)
        (home / "retained.txt").write_text("rival")
        return plan
    monkeypatch.setattr(dist, "plan_install", staged)
    original_copy = dist._copy_dist_payload
    def copy(*args, **kwargs):
        assert_reserved(home)
        return original_copy(*args, **kwargs)
    monkeypatch.setattr(dist, "_copy_dist_payload", copy)
    if force:
        dist.install_distribution(str(source), force=True)
        assert (home / "retained.txt").read_text() == "rival"
    else:
        with pytest.raises(dist.DistributionError, match="already exists"):
            dist.install_distribution(str(source))
        assert not (home / "distribution.yaml").exists()


@pytest.mark.parametrize("transition", ["retired", "replaced", "unchanged"])
def test_distribution_update_revalidates_home_after_staging(tmp_path, monkeypatch, transition):
    from hermes_cli import profile_distribution as dist
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    source = tmp_path / "distribution"
    source.mkdir()
    dist.write_manifest(source, dist.DistributionManifest(name="updated", version="0.1.0"))
    dist.install_distribution(str(source))
    home = profiles.get_profile_dir("updated")
    original_plan = dist.plan_install
    def staged(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        if transition != "unchanged":
            home.rename(home.with_name("retained-old"))
        if transition == "replaced":
            home.mkdir()
            (home / "profile.yaml").write_text("name: updated\n")
            (home / "retained.txt").write_text("replacement")
        return plan
    monkeypatch.setattr(dist, "plan_install", staged)
    original_copy = dist._copy_dist_payload
    def copy(*args, **kwargs):
        assert_reserved(home)
        return original_copy(*args, **kwargs)
    monkeypatch.setattr(dist, "_copy_dist_payload", copy)
    if transition == "unchanged":
        dist.update_distribution("updated")
        assert (home / "distribution.yaml").exists()
    else:
        with pytest.raises(dist.DistributionError):
            dist.update_distribution("updated")
        if transition == "retired":
            assert not home.exists()
        else:
            assert (home / "retained.txt").read_text() == "replacement"
            assert not (home / "distribution.yaml").exists()


def test_import_waits_for_competing_publisher_then_refuses_existing_target(tmp_path, monkeypatch):
    """The real import consumer must recheck existence after waiting for the name."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    import tarfile
    import threading
    from hermes_cli.profiles_lifecycle import _lock_path

    root = tmp_path / "root"
    home = root / "profiles" / "contended"
    source = tmp_path / "source"
    source.mkdir()
    (source / "profile.yaml").write_text("name: contended\n")
    archive = tmp_path / "profile.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="contended")
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: root / "profiles")
    lock = _lock_path(home)
    lock.parent.mkdir(parents=True)
    peer = subprocess.Popen([sys.executable, "-c", r"""
import fcntl, pathlib, sys
with open(sys.argv[1], 'a+b') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print('reserved', flush=True)
    sys.stdin.readline()
    home = pathlib.Path(sys.argv[2])
    home.mkdir(parents=True)
    (home / 'profile.yaml').write_text('name: contended\n')
""", str(lock), str(home)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    attempting = threading.Event()
    original_reservation = profiles.profile_lifecycle_reservation
    @contextmanager
    def observed_reservation(*homes):
        attempting.set()
        with original_reservation(*homes):
            yield
    monkeypatch.setattr(profiles, "profile_lifecycle_reservation", observed_reservation)
    try:
        assert peer.stdout.readline().strip() == "reserved"
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(profiles.import_profile, str(archive))
            try:
                assert attempting.wait(5), "import did not attempt the shared reservation"
            finally:
                _, errors = peer.communicate("publish\n", timeout=5)
                assert peer.returncode == 0, errors
            with pytest.raises(FileExistsError):
                future.result(timeout=5)
        assert (home / "profile.yaml").read_text() == "name: contended\n"
    finally:
        if peer.poll() is None:
            peer.kill()
            peer.communicate(timeout=5)


def test_distribution_update_refuses_tombstoned_same_inode_after_staging(tmp_path, monkeypatch):
    """A failed deletion retains its directory while its tombstone forbids updates."""
    from hermes_cli import profile_distribution as dist
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    source = tmp_path / "distribution"
    source.mkdir()
    (source / "config.yaml").write_text("model: fixture\n")
    (source / "SOUL.md").write_text("before\n")
    dist.write_manifest(source, dist.DistributionManifest(name="retained", version="0.1.0"))
    dist.install_distribution(str(source))
    home = profiles.get_profile_dir("retained")
    identity = home.stat()
    original_plan = dist.plan_install
    (source / "SOUL.md").write_text("after\n")
    def staged(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        # delete_profile tombstones before trying rmtree; a removal failure keeps this inode.
        profiles.mark_named_profile_deleted(home)
        assert home.stat().st_ino == identity.st_ino
        return plan
    monkeypatch.setattr(dist, "plan_install", staged)
    with pytest.raises((dist.DistributionError, FileNotFoundError)):
        dist.update_distribution("retained")
    assert profiles.named_profile_is_deleted(home)
    assert home.stat().st_ino == identity.st_ino
    assert (home / "SOUL.md").read_text() == "before\n"
