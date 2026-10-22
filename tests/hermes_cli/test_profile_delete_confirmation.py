"""Profile confirmation never reserves a name or authorizes its replacement."""

from pathlib import Path
import subprocess
import sys

import pytest

from hermes_cli import profiles
from hermes_cli.profiles_lifecycle import _lock_path, profile_lifecycle_reservation


@pytest.fixture
def profile(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    home = root / "profiles" / "reviewed"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("model: original\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(profiles, "_check_gateway_running", lambda *_: False)
    monkeypatch.setattr(profiles, "_find_our_wrapper", lambda *_: None)
    monkeypatch.setattr(profiles, "print_delete_summary", lambda *_: None)
    for name in (
        "_cleanup_gateway_service", "_maybe_unregister_gateway_service",
        "_stop_profile_backends", "_stop_bot_desktop", "_notify_multiplexer",
    ):
        monkeypatch.setattr(profiles, name, lambda *_: None)
    monkeypatch.setattr(profiles, "_purge_identity", lambda *_: True)
    return home


@pytest.mark.platforms("posix")
def test_waiting_for_confirmation_leaves_cross_process_reservation_available(profile, monkeypatch):
    def cancel_after_probe(_prompt):
        lock = _lock_path(profile)
        lock.parent.mkdir(parents=True, exist_ok=True)
        probe = (
            "import fcntl,sys; f=open(sys.argv[1],'a+b'); "
            "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe, str(lock)], capture_output=True, check=False, timeout=10
        )
        assert result.returncode == 0, result.stderr
        return "cancel"

    monkeypatch.setattr("builtins.input", cancel_after_probe)
    assert profiles.delete_profile("reviewed") == profile
    assert profile.is_dir()


def test_confirmation_refuses_same_name_replacement(profile, monkeypatch):
    effects = []
    monkeypatch.setattr(profiles, "_cleanup_gateway_service", lambda *_: effects.append("cleanup"))

    def replace_before_confirmation(_prompt):
        with profile_lifecycle_reservation(profile):
            profile.rename(profile.with_name("original-retained"))
            profile.mkdir()
            (profile / "config.yaml").write_text("model: replacement\n", encoding="utf-8")
        return "reviewed"

    monkeypatch.setattr("builtins.input", replace_before_confirmation)
    with pytest.raises(RuntimeError, match="changed"):
        profiles.delete_profile("reviewed")
    assert (profile / "config.yaml").read_text(encoding="utf-8") == "model: replacement\n"
    assert effects == []
    assert not profiles.named_profile_is_deleted(profile)


def test_confirmation_refreshes_gateway_state_before_mutation(profile, monkeypatch):
    states = iter((False, True))
    monkeypatch.setattr(profiles, "_check_gateway_running", lambda *_: next(states))
    monkeypatch.setattr("builtins.input", lambda _: "reviewed")
    stopped = []
    monkeypatch.setattr(profiles, "_stop_gateway_process", lambda home: stopped.append(home))

    profiles.delete_profile("reviewed")

    assert stopped == [profile]
    assert not profile.exists()


@pytest.mark.parametrize("answer", ["cancel", KeyboardInterrupt, EOFError])
def test_cancelled_confirmation_preserves_profile(profile, monkeypatch, answer):
    def cancel(_prompt):
        if isinstance(answer, type):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", cancel)
    assert profiles.delete_profile("reviewed") == profile
    assert profile.is_dir()
    assert not profiles.named_profile_is_deleted(profile)
