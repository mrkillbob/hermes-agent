"""Reclaiming an orphan install state never leaves two holders of its install lock
(``pm/install_states.py``, ``hermes_cli.runtime_state.runtime_lock``)."""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

import pm.environments
import pm.install_states
import pm.lifetime
import pytest
from hermes_cli.runtime_state import runtime_lock
from pm.environments import install_key
from pm.install_states import collect_orphan_install_states


@pytest.mark.platforms("posix")
def test_launcher_waiting_on_a_reclaimed_state_lock_excludes_later_launchers(tmp_path, monkeypatch):
    """A launcher cannot open the state lock until the collector releases its outer gate.
    Once it obtains the new lock, a second launcher must still be excluded."""
    installs = tmp_path / "home" / "installs"
    checkout = tmp_path / "home" / "cache" / "scratch" / "clone"  # provably deleted: reclaimed
    state = installs / install_key(checkout)
    with pm.lifetime.activity(state, create=True):
        (state / "inputs").mkdir(parents=True)
        (state / "inputs" / ".project-root").write_text(str(checkout), encoding="utf-8")
        (state / "environments" / "gen" / "venv").mkdir(parents=True)
    monkeypatch.setattr(pm.environments, "installs_root", lambda: installs)

    opened, held, done = (threading.Event() for _ in range(3))
    contender_outcome = []
    real_open = os.open

    def observed_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if threading.current_thread().name == "contender" and os.fspath(path).endswith(".install.lock"):
            opened.set()
        return fd

    def contender():
        try:
            with runtime_lock(checkout, timeout=0) as got:
                contender_outcome.append(got)
        except pm.lifetime.LifetimeUnavailable:
            contender_outcome.append("gate refused")

    def launcher_a():
        with runtime_lock(checkout, timeout=None) as got:
            if got:
                held.set()
            done.wait(30)

    a = threading.Thread(target=launcher_a, name="launcher-a", daemon=True)
    real_leased = pm.install_states._leased

    def checkout_recreated_mid_reclaim(path):
        # Called under the collector's lock, after its orphan re-check.
        checkout.mkdir(parents=True)
        waiting = threading.Thread(target=contender, name="contender", daemon=True)
        waiting.start()
        waiting.join(5)
        assert not waiting.is_alive() and contender_outcome == ["gate refused"]
        assert not opened.is_set(), "contender opened the install lock under the collector's gate"
        return real_leased(path)

    monkeypatch.setattr(os, "open", observed_open)
    monkeypatch.setattr(pm.install_states, "_leased", checkout_recreated_mid_reclaim)
    try:
        assert collect_orphan_install_states(installs) == [state]
        assert contender_outcome == ["gate refused"]
        a.start()
        assert held.wait(5)
        with runtime_lock(checkout, timeout=0) as launcher_b:
            assert launcher_b is False, "two launchers hold the same state's install lock"
    finally:
        done.set()
        if a.ident is not None:
            a.join(5)


@pytest.mark.platforms("posix")
def test_lock_file_removal_never_lands_after_a_waiting_launcher_got_the_lock(tmp_path, monkeypatch):
    """The collector unlinks an existing install lock before releasing the outer gate.
    A waiting launcher cannot open either the old or replacement lock prematurely."""
    installs = tmp_path / "home" / "installs"
    checkout = tmp_path / "home" / "cache" / "scratch" / "clone"
    state = installs / install_key(checkout)
    with pm.lifetime.activity(state, create=True):
        (state / "inputs").mkdir(parents=True)
        (state / "inputs" / ".project-root").write_text(str(checkout), encoding="utf-8")
        (state / "environments" / "gen" / "venv").mkdir(parents=True)
        (state / ".install.lock").touch()
    monkeypatch.setattr(pm.environments, "installs_root", lambda: installs)

    opened, unlinked, held, released, done = (threading.Event() for _ in range(5))
    contender_outcome = []
    collector = threading.current_thread()
    real_open, real_unlink, real_gate = os.open, Path.unlink, pm.lifetime.gate

    def observed_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if threading.current_thread().name == "contender" and os.fspath(path).endswith(".install.lock"):
            opened.set()
        return fd

    @contextlib.contextmanager
    def observed_gate(path, **kwargs):
        with real_gate(path, **kwargs) as guard:
            yield guard
        if threading.current_thread() is collector:
            released.set()

    def unlink_late(self, *args, **kwargs):
        if threading.current_thread() is collector and self.name == ".install.lock":
            assert not released.is_set(), "install lock removed after the collector's gate"
            assert not opened.is_set(), "launcher opened the old install lock before removal"
            unlinked.set()
        return real_unlink(self, *args, **kwargs)

    def contender():
        try:
            with runtime_lock(checkout, timeout=0) as got:
                contender_outcome.append(got)
        except pm.lifetime.LifetimeUnavailable:
            contender_outcome.append("gate refused")

    def launcher_a():
        with runtime_lock(checkout, timeout=None) as got:
            if got:
                held.set()
            done.wait(30)

    a = threading.Thread(target=launcher_a, name="launcher-a", daemon=True)
    real_leased = pm.install_states._leased

    def checkout_recreated_mid_reclaim(path):
        checkout.mkdir(parents=True)
        waiting = threading.Thread(target=contender, name="contender", daemon=True)
        waiting.start()
        waiting.join(5)
        assert not waiting.is_alive() and contender_outcome == ["gate refused"]
        assert not opened.is_set(), "contender opened the old install lock under the collector's gate"
        return real_leased(path)

    monkeypatch.setattr(os, "open", observed_open)
    monkeypatch.setattr(Path, "unlink", unlink_late)
    monkeypatch.setattr(pm.lifetime, "gate", observed_gate)
    monkeypatch.setattr(pm.install_states, "_leased", checkout_recreated_mid_reclaim)
    try:
        assert collect_orphan_install_states(installs) == [state]
        assert contender_outcome == ["gate refused"]
        assert unlinked.is_set()
        assert released.is_set()
        a.start()
        assert held.wait(5)
        with runtime_lock(checkout, timeout=0) as launcher_b:
            assert launcher_b is False, "two launchers hold the same state's install lock"
    finally:
        done.set()
        if a.ident is not None:
            a.join(5)


@pytest.mark.platforms("posix")
def test_unopenable_lock_path_fails_instead_of_retrying(tmp_path, monkeypatch):
    """A lock path that can never be opened (a dangling symlink) raises at once; only a parent
    directory removed mid-open is worth retrying, and never past the timeout."""
    installs = tmp_path / "installs"
    checkout = tmp_path / "clone"
    monkeypatch.setattr(pm.environments, "installs_root", lambda: installs)
    lock = installs / install_key(checkout) / ".install.lock"
    lock.parent.mkdir(parents=True)
    lock.symlink_to(tmp_path / "gone" / "target")
    outcome: list[BaseException] = []

    def launcher():
        try:
            with runtime_lock(checkout, timeout=0):
                pass
        except BaseException as exc:
            outcome.append(exc)

    t = threading.Thread(target=launcher, daemon=True)
    t.start()
    t.join(30)
    assert not t.is_alive(), "lock_path retries an open that can never succeed"
    assert len(outcome) == 1 and isinstance(outcome[0], FileNotFoundError)
